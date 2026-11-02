//go:build integration

package stream

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"database/sql"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/segmentio/kafka-go"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/postgres"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

func containerPin(t *testing.T, name string) string {
	t.Helper()
	pins, err := os.ReadFile("../../../../.tool-versions")
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(string(pins), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && fields[0] == name {
			return fields[1]
		}
	}
	t.Fatalf("missing container pin %s", name)
	return ""
}

func startContainer(t *testing.T, ctx context.Context, request testcontainers.ContainerRequest) testcontainers.Container {
	t.Helper()
	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{ContainerRequest: request})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := container.Terminate(cleanup); err != nil {
			t.Errorf("terminate test container: %v", err)
		}
	})
	if err := container.Start(ctx); err != nil {
		if logs, logErr := container.Logs(ctx); logErr == nil {
			data, _ := io.ReadAll(io.LimitReader(logs, 12000))
			_ = logs.Close()
			t.Logf("synthetic integration container startup: %s", data)
		}
		t.Fatal(err)
	}
	return container
}

func TestKafkaCrashRecovery(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	previous := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	t.Cleanup(func() { otel.SetTracerProvider(previous) })
	ctx, origin := telemetry.Start(ctx, "synthetic payment request", trace.SpanKindServer)
	defer origin.End()
	pg := startContainer(t, ctx, testcontainers.ContainerRequest{
		Image: "postgres:" + containerPin(t, "postgres"), ExposedPorts: []string{"5432/tcp"},
		Env:        map[string]string{"POSTGRES_USER": "synthetic", "POSTGRES_PASSWORD": "integration-only", "POSTGRES_DB": "payments"},
		WaitingFor: wait.ForLog("database system is ready to accept connections").WithOccurrence(2),
	})
	pgHost, err := pg.Host(ctx)
	if err != nil {
		t.Fatal(err)
	}
	pgPort, err := pg.MappedPort(ctx, "5432/tcp")
	if err != nil {
		t.Fatal(err)
	}
	dsn := fmt.Sprintf("postgres://synthetic:integration-only@%s:%s/payments?sslmode=disable", pgHost, pgPort.Port())
	store, err := postgres.Open(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	broker := startContainer(t, ctx, testcontainers.ContainerRequest{
		Image: "apache/kafka:" + containerPin(t, "kafka"), ExposedPorts: []string{"9092/tcp"},
		Env:        map[string]string{"KAFKA_HEAP_OPTS": "-Xms128m -Xmx384m", "LOG_DIR": "/tmp/logs"},
		Entrypoint: []string{"/bin/bash", "-ec"}, Cmd: []string{`while [ ! -f /tmp/test-ready ]; do sleep 0.1; done
/opt/kafka/bin/kafka-storage.sh format --cluster-id dGVzdC1jbHVzdGVyLTAwMDE --config /tmp/test.properties --add-scram 'SCRAM-SHA-512=[name=synthetic,password=integration-only]'
exec /opt/kafka/bin/kafka-server-start.sh /tmp/test.properties`},
	})
	host, err := broker.Host(ctx)
	if err != nil {
		t.Fatal(err)
	}
	port, err := broker.MappedPort(ctx, "9092/tcp")
	if err != nil {
		t.Fatal(err)
	}
	address := net.JoinHostPort(host, port.Port())
	cert, key := testCertificate(t, host)
	caFile := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(caFile, cert, 0600); err != nil {
		t.Fatal(err)
	}
	properties := fmt.Sprintf(`process.roles=broker,controller
node.id=1
controller.quorum.voters=1@127.0.0.1:9093
controller.listener.names=CONTROLLER
listeners=SASL_SSL://0.0.0.0:9092,CONTROLLER://127.0.0.1:9093
advertised.listeners=SASL_SSL://%s
listener.security.protocol.map=SASL_SSL:SASL_SSL,CONTROLLER:PLAINTEXT
inter.broker.listener.name=SASL_SSL
sasl.enabled.mechanisms=SCRAM-SHA-512
sasl.mechanism.inter.broker.protocol=SCRAM-SHA-512
listener.name.sasl_ssl.scram-sha-512.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="synthetic" password="integration-only";
ssl.keystore.type=PEM
ssl.keystore.location=/tmp/server.pem
ssl.truststore.type=PEM
ssl.truststore.location=/tmp/ca.pem
log.dirs=/tmp/kafka-data
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
auto.create.topics.enable=false
group.initial.rebalance.delay.ms=0
`, address)
	for path, data := range map[string][]byte{"/tmp/test.properties": []byte(properties), "/tmp/server.pem": append(key, cert...), "/tmp/ca.pem": cert} {
		if err := broker.CopyToContainer(ctx, data, path, 0644); err != nil {
			t.Fatal(err)
		}
	}
	if err := broker.CopyToContainer(ctx, []byte("ready"), "/tmp/test-ready", 0644); err != nil {
		t.Fatal(err)
	}
	if err := wait.ForLog("Kafka Server started").WithStartupTimeout(time.Minute).WaitUntilReady(ctx, broker); err != nil {
		t.Fatal(err)
	}
	registryContainer := startContainer(t, ctx, testcontainers.ContainerRequest{
		Image:        "apicurio/apicurio-registry:" + containerPin(t, "apicurio"),
		ExposedPorts: []string{"8080/tcp", "9000/tcp"},
		Env:          map[string]string{"JAVA_OPTS_APPEND": "-Xms64m -Xmx256m"},
		WaitingFor:   wait.ForHTTP("/health/ready").WithPort("9000/tcp").WithStartupTimeout(time.Minute),
	})
	registryHost, err := registryContainer.Host(ctx)
	if err != nil {
		t.Fatal(err)
	}
	registryPort, err := registryContainer.MappedPort(ctx, "8080/tcp")
	if err != nil {
		t.Fatal(err)
	}
	base := fmt.Sprintf("http://%s:%s/apis/ccompat/v7", registryHost, registryPort.Port())
	registry, err := NewRegistry(base)
	if err != nil {
		t.Fatal(err)
	}
	schema, err := os.ReadFile("../../../event-contracts/schemas/payment-initiated-v1.avsc")
	if err != nil {
		t.Fatal(err)
	}
	const topic = "payments.initiated.v1"
	id, codec, err := registry.Register(ctx, topic+"-value", string(schema))
	if err != nil {
		t.Fatal(err)
	}
	reusedID, _, err := registry.Register(ctx, topic+"-value", string(schema))
	if err != nil || reusedID != id {
		t.Fatalf("schema registration was not idempotent: %d %v", reusedID, err)
	}
	breaking := strings.Replace(string(schema), `"name": "event_id"`, `"name": "new_required_id"`, 1)
	if _, _, err := registry.Register(ctx, topic+"-value", breaking); err == nil {
		t.Fatal("registry accepted a backward-incompatible schema")
	}
	k, err := NewKafka("127.0.0.1:1,"+address, caFile, "synthetic", "integration-only")
	if err != nil {
		t.Fatal(err)
	}
	defer k.Close()
	if err := k.EnsureTopic(ctx, topic); err != nil {
		t.Fatal(err)
	}
	if err := k.EnsureTopic(ctx, topic); err != nil {
		t.Fatalf("topic setup is not idempotent: %v", err)
	}
	if err := k.SetReplicationFactor(3); err != nil {
		t.Fatal(err)
	}
	if err := k.EnsureTopic(ctx, topic); err == nil {
		t.Fatal("existing single-replica topic silently accepted as replication factor three")
	}
	if err := k.SetReplicationFactor(1); err != nil {
		t.Fatal(err)
	}
	unauthorized, err := NewKafka(address, caFile, "synthetic", "wrong-password")
	if err != nil {
		t.Fatal(err)
	}
	if err := unauthorized.EnsureTopic(ctx, topic); err == nil {
		t.Fatal("Kafka accepted invalid SCRAM credentials")
	}
	if err := unauthorized.Close(); err != nil {
		t.Fatal(err)
	}
	p, err := store.Create(ctx, "recovery", payment.Command{AccountID: "12345678-1234-4234-8234-123456789abc", AmountMinor: 1250, Currency: "USD"})
	if err != nil {
		t.Fatal(err)
	}
	child := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestPublisherCrashProcess$")
	child.Env = append(os.Environ(), "RPP_CRASH_CHILD=1", "DATABASE_URL="+dsn, "KAFKA_BROKERS="+address,
		"KAFKA_CA_FILE="+caFile, "KAFKA_USERNAME=synthetic", "KAFKA_PASSWORD=integration-only", "REGISTRY_URL="+base)
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if child.ProcessState == nil {
			_ = child.Process.Kill()
			_ = child.Wait()
		}
	})
	reader := k.Reader(topic, "crash-proof")
	defer reader.Close()
	first, err := reader.FetchMessage(ctx)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := codec.Decode(first.Value)
	if err != nil || decoded.Payload.PaymentID != p.ID {
		t.Fatalf("first publish: %+v %v", decoded, err)
	}
	inserted, err := store.RecordReceipt(ctx, postgres.Publication{Event: decoded, AccountID: string(first.Key)})
	if err != nil || !inserted {
		t.Fatalf("first receipt: %v %v", inserted, err)
	}
	if err := child.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	if err := child.Wait(); err == nil {
		t.Fatal("crash publisher exited normally")
	}
	result, err := store.RelayOne(ctx, 8, func(ctx context.Context, message postgres.Publication) error {
		return k.Publish(ctx, topic, id, codec, message)
	})
	if err != nil || result.State != "published" {
		t.Fatalf("publisher restart: %+v %v", result, err)
	}
	second, err := reader.FetchMessage(ctx)
	if err != nil {
		t.Fatal(err)
	}
	redelivered, err := codec.Decode(second.Value)
	if err != nil || redelivered != decoded || string(second.Key) != p.AccountID {
		t.Fatalf("unstable redelivery: %+v %v", redelivered, err)
	}
	headers := Headers(second.Headers)
	producerContext := trace.SpanContextFromContext(telemetry.Propagator.Extract(context.Background(), &headers))
	if producerContext.TraceID() != origin.SpanContext().TraceID() || !producerContext.IsRemote() {
		t.Fatal("real Kafka redelivery lost the original request trace")
	}
	inserted, err = store.RecordReceipt(ctx, postgres.Publication{Event: redelivered, AccountID: string(second.Key)})
	if err != nil || inserted {
		t.Fatalf("duplicate effect: %v %v", inserted, err)
	}
	consumerCtx, stop := context.WithCancel(ctx)
	defer stop()
	done := make(chan error, 1)
	go func() { done <- RunConsumer(consumerCtx, store, k, registry, topic) }()
	if err := k.writer.WriteMessages(ctx, kafka.Message{Topic: topic, Key: []byte(p.AccountID), Value: []byte("invalid-avro")}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(20 * time.Second)
	for {
		var count int
		if err := db.QueryRowContext(ctx, "SELECT count(*) FROM payment_event_dead_letters").Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count == 1 {
			break
		}
		if time.Now().After(deadline) {
			stop()
			t.Fatal("poison event was not quarantined")
		}
		time.Sleep(100 * time.Millisecond)
	}
	stop()
	<-done
	var count int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM payment_event_receipts").Scan(&count); err != nil || count != 1 {
		t.Fatalf("not exactly one consumer effect after broker replay: %d %v", count, err)
	}
	correlated := false
	for _, span := range recorder.Ended() {
		if span.Name() == "payment.initiated process" && span.Parent().SpanID() == producerContext.SpanID() &&
			span.SpanContext().TraceID() == origin.SpanContext().TraceID() {
			correlated = true
		}
	}
	if !correlated {
		t.Fatal("consumer processing span was not a child of the redelivered Kafka producer")
	}
}

func TestPublisherCrashProcess(t *testing.T) {
	if os.Getenv("RPP_CRASH_CHILD") != "1" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	store, err := postgres.Open(os.Getenv("DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	k, err := NewKafka(os.Getenv("KAFKA_BROKERS"), os.Getenv("KAFKA_CA_FILE"), os.Getenv("KAFKA_USERNAME"), os.Getenv("KAFKA_PASSWORD"))
	if err != nil {
		t.Fatal(err)
	}
	defer k.Close()
	registry, err := NewRegistry(os.Getenv("REGISTRY_URL"))
	if err != nil {
		t.Fatal(err)
	}
	schema, err := os.ReadFile("../../../event-contracts/schemas/payment-initiated-v1.avsc")
	if err != nil {
		t.Fatal(err)
	}
	id, codec, err := registry.Register(ctx, "payments.initiated.v1-value", string(schema))
	if err != nil {
		t.Fatal(err)
	}
	_, err = store.RelayOne(ctx, 8, func(ctx context.Context, message postgres.Publication) error {
		if err := k.Publish(ctx, "payments.initiated.v1", id, codec, message); err != nil {
			return err
		}
		<-ctx.Done()
		return ctx.Err()
	})
	if err != nil {
		t.Fatal(err)
	}
}

func testCertificate(t *testing.T, host string) ([]byte, []byte) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "synthetic-integration"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true,
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, DNSNames: []string{host, "localhost"},
	}
	if ip := net.ParseIP(host); ip != nil {
		template.IPAddresses = []net.IP{ip}
	}
	cert, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert}), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encoded})
}
