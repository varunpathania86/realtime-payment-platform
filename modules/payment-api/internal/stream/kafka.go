package stream

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/segmentio/kafka-go"
	"github.com/segmentio/kafka-go/sasl/scram"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/event"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/postgres"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

type Kafka struct {
	replicationFactor int
	brokers           []string
	dialer            *kafka.Dialer
	writer            *kafka.Writer
}

func (k *Kafka) SetReplicationFactor(factor int) error {
	if factor != 1 && factor != 3 {
		return errors.New("Kafka replication factor must be 1 or 3")
	}
	k.replicationFactor = factor
	return nil
}

func NewKafka(brokers, caFile, username, password string) (*Kafka, error) {
	if brokers == "" || caFile == "" || username == "" || password == "" {
		return nil, errors.New("Kafka brokers, CA file, and SCRAM credentials are required")
	}
	ca, err := os.ReadFile(caFile)
	if err != nil {
		return nil, errors.New("cannot read Kafka CA certificate")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(ca) {
		return nil, errors.New("invalid Kafka CA certificate")
	}
	mechanism, err := scram.Mechanism(scram.SHA512, username, password)
	if err != nil {
		return nil, err
	}
	security := &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots}
	dialer := &kafka.Dialer{Timeout: 5 * time.Second, TLS: security, SASLMechanism: mechanism}
	addresses := strings.Split(brokers, ",")
	for _, address := range addresses {
		if strings.TrimSpace(address) != address || address == "" {
			return nil, errors.New("invalid Kafka broker list")
		}
	}
	return &Kafka{replicationFactor: 1, brokers: addresses, dialer: dialer, writer: &kafka.Writer{
		Addr: kafka.TCP(addresses...), Balancer: &kafka.Hash{}, RequiredAcks: kafka.RequireAll,
		MaxAttempts: 1, BatchSize: 1, ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second,
		Transport: &kafka.Transport{TLS: security, SASL: mechanism, DialTimeout: 5 * time.Second},
	}}, nil
}

func (k *Kafka) Close() error {
	err := k.writer.Close()
	if transport, ok := k.writer.Transport.(*kafka.Transport); ok {
		transport.CloseIdleConnections()
	}
	return err
}

func (k *Kafka) EnsureTopic(ctx context.Context, topic string) error {
	var connection *kafka.Conn
	for _, address := range k.brokers {
		attempt, cancel := context.WithTimeout(ctx, 2*time.Second)
		candidate, err := k.dialer.DialContext(attempt, "tcp", address)
		cancel()
		if err == nil {
			connection = candidate
			break
		}
	}
	if connection == nil {
		return errors.New("Kafka broker unavailable")
	}
	defer connection.Close()
	if err := connection.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
		return err
	}
	controller, err := connection.Controller()
	if err != nil {
		return errors.New("Kafka controller unavailable")
	}
	admin, err := k.dialer.DialContext(ctx, "tcp", fmt.Sprintf("%s:%d", controller.Host, controller.Port))
	if err != nil {
		return errors.New("Kafka controller connection failed")
	}
	defer admin.Close()
	if err := admin.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
		return err
	}
	minISR := "1"
	if k.replicationFactor == 3 {
		minISR = "2"
	}
	if err := admin.CreateTopics(kafka.TopicConfig{Topic: topic, NumPartitions: 3, ReplicationFactor: k.replicationFactor,
		ConfigEntries: []kafka.ConfigEntry{{ConfigName: "min.insync.replicas", ConfigValue: minISR}},
	}); err != nil {
		return errors.New("Kafka topic creation failed")
	}
	partitions, err := admin.ReadPartitions(topic)
	if err != nil {
		return errors.New("Kafka topic metadata unavailable")
	}
	if err := validateReplicas(partitions, k.replicationFactor); err != nil {
		return err
	}
	client := &kafka.Client{Addr: kafka.TCP(fmt.Sprintf("%s:%d", controller.Host, controller.Port)), Transport: k.writer.Transport}
	config, err := client.DescribeConfigs(ctx, &kafka.DescribeConfigsRequest{Resources: []kafka.DescribeConfigRequestResource{{
		ResourceType: kafka.ResourceTypeTopic, ResourceName: topic, ConfigNames: []string{"min.insync.replicas"},
	}}})
	if err != nil {
		return errors.New("Kafka topic configuration unavailable")
	}
	for _, resource := range config.Resources {
		if resource.Error != nil {
			return errors.New("Kafka topic configuration rejected")
		}
		for _, entry := range resource.ConfigEntries {
			if entry.ConfigName == "min.insync.replicas" && entry.ConfigValue == minISR {
				return nil
			}
		}
	}
	return errors.New("Kafka topic min.insync.replicas mismatch; explicit operator correction required")
}

func validateReplicas(partitions []kafka.Partition, factor int) error {
	if len(partitions) != 3 {
		return errors.New("Kafka topic must have three partitions; explicit operator correction required")
	}
	for _, partition := range partitions {
		if len(partition.Replicas) != factor {
			return errors.New("Kafka topic replication factor mismatch; explicit replica reassignment required")
		}
	}
	return nil
}

func (k *Kafka) Publish(ctx context.Context, topic string, id uint32, codec *event.Codec, message postgres.Publication) error {
	ctx, span := telemetry.Start(ctx, "payment.initiated publish", trace.SpanKindProducer,
		attribute.String("messaging.system", "kafka"),
		attribute.String("messaging.destination.name", topic),
		attribute.String("messaging.operation.type", "send"),
		attribute.String("event_id", message.Event.ID),
		attribute.String("payment_id", message.Event.Payload.PaymentID))
	defer span.End()
	start := time.Now()
	data, err := codec.Encode(id, message.Event)
	if err != nil {
		span.SetStatus(codes.Error, "event_encoding_failed")
		return err
	}
	headers := Headers{{Key: "event_id", Value: []byte(message.Event.ID)}}
	telemetry.Propagator.Inject(ctx, &headers)
	err = k.writer.WriteMessages(ctx, kafka.Message{
		Topic: topic, Key: []byte(message.AccountID), Value: data,
		Headers: []kafka.Header(headers),
	})
	if err != nil {
		span.SetStatus(codes.Error, "publication_failed")
	}
	outcome := "success"
	if err != nil {
		outcome = "error"
	}
	telemetry.Observe(ctx, telemetry.PublishDuration.WithLabelValues(outcome), time.Since(start).Seconds())
	slog.InfoContext(ctx, "kafka_publication", "event_id", message.Event.ID,
		"payment_id", message.Event.Payload.PaymentID, "success", err == nil)
	return err
}

type Headers []kafka.Header

func (h *Headers) Get(key string) string {
	for _, header := range *h {
		if header.Key == key {
			return string(header.Value)
		}
	}
	return ""
}

func (h *Headers) Set(key, value string) {
	for index := range *h {
		if (*h)[index].Key == key {
			(*h)[index].Value = []byte(value)
			return
		}
	}
	*h = append(*h, kafka.Header{Key: key, Value: []byte(value)})
}

func (h *Headers) Keys() []string {
	keys := make([]string, len(*h))
	for index, header := range *h {
		keys[index] = header.Key
	}
	return keys
}

func (k *Kafka) Reader(topic, group string) *kafka.Reader {
	return kafka.NewReader(kafka.ReaderConfig{
		Brokers: k.brokers, Topic: topic, GroupID: group, Dialer: k.dialer,
		MinBytes: 1, MaxBytes: 1 << 20, MaxWait: time.Second,
		CommitInterval: 0, StartOffset: kafka.FirstOffset,
		WatchPartitionChanges: true,
	})
}
