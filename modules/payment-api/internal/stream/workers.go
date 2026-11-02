package stream

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"time"

	"github.com/segmentio/kafka-go"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/event"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/postgres"
)

func pause(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func RunRelay(ctx context.Context, store *postgres.Store, k *Kafka, registry *Registry, topic, schemaFile string) error {
	schema, err := os.ReadFile(schemaFile)
	if err != nil {
		return errors.New("cannot read authoritative Avro schema file")
	}
	setup, cancel := context.WithTimeout(ctx, 20*time.Second)
	id, codec, err := registry.Register(setup, topic+"-value", string(schema))
	cancel()
	if err != nil {
		return err
	}
	setup, cancel = context.WithTimeout(ctx, 15*time.Second)
	err = k.EnsureTopic(setup, topic)
	cancel()
	if err != nil {
		return err
	}
	for ctx.Err() == nil {
		attempt, cancel := context.WithTimeout(ctx, 10*time.Second)
		result, err := store.RelayOne(attempt, 8, func(ctx context.Context, message postgres.Publication) error {
			return k.Publish(ctx, topic, id, codec, message)
		})
		cancel()
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			slog.Error("outbox_storage_failed", "error_type", "dependency_unavailable")
		} else if result.EventID != "" {
			if result.State == "published" {
				slog.Info("outbox_published", "event_id", result.EventID, "attempts", result.Attempts)
			} else {
				slog.Error("outbox_publication_failed", "event_id", result.EventID, "state", result.State, "attempts", result.Attempts, "failure_code", "publication_failed")
			}
		}
		if err != nil || result.EventID == "" {
			if err := pause(ctx, time.Second); err != nil {
				break
			}
		}
	}
	return ctx.Err()
}

func RunConsumer(ctx context.Context, store *postgres.Store, k *Kafka, registry *Registry, topic string) error {
	reader := k.Reader(topic, "payment-receipts-v1")
	defer reader.Close()
	codecs := make(map[uint32]*event.Codec)
	quarantine := func(message kafka.Message, code string) error {
		write, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		if err := store.Quarantine(write, message.Topic, message.Partition, message.Offset, message.Key, message.Value, code); err != nil {
			return errors.New("dead-letter persistence failed; offset not committed")
		}
		if err := reader.CommitMessages(write, message); err != nil {
			return errors.New("dead-letter offset commit failed; replay is safe")
		}
		slog.Error("event_quarantined", "topic", message.Topic, "partition", message.Partition, "offset", message.Offset, "failure_code", code)
		return nil
	}
	for ctx.Err() == nil {
		message, err := reader.FetchMessage(ctx)
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			return errors.New("Kafka receipt consumer fetch failed")
		}
		id, err := event.SchemaID(message.Value)
		if err != nil {
			if err := quarantine(message, "invalid_avro"); err != nil {
				return err
			}
			continue
		}
		codec := codecs[id]
		if codec == nil {
			if len(codecs) >= 128 {
				return errors.New("schema cache limit exceeded; offset not committed")
			}
			lookup, cancel := context.WithTimeout(ctx, 5*time.Second)
			codec, err = registry.Codec(lookup, id)
			cancel()
			if err != nil {
				return err
			}
			codecs[id] = codec
		}
		e, err := codec.Decode(message.Value)
		if err != nil {
			if err := quarantine(message, "invalid_avro"); err != nil {
				return err
			}
			continue
		}
		if err := e.Validate(string(message.Key)); err != nil {
			if err := quarantine(message, "invalid_event"); err != nil {
				return err
			}
			continue
		}
		write, cancel := context.WithTimeout(ctx, 5*time.Second)
		inserted, err := store.RecordReceipt(write, postgres.Publication{Event: e, AccountID: string(message.Key)})
		cancel()
		if errors.Is(err, postgres.ErrReceiptConflict) {
			if err := quarantine(message, "identity_conflict"); err != nil {
				return err
			}
			continue
		}
		if err != nil {
			return errors.New("receipt persistence failed; offset not committed")
		}
		ack, cancel := context.WithTimeout(ctx, 5*time.Second)
		err = reader.CommitMessages(ack, message)
		cancel()
		if err != nil {
			return errors.New("receipt offset commit failed; replay is safe")
		}
		slog.Info("payment_receipt", "event_id", e.ID, "payment_id", e.Payload.PaymentID, "inserted", inserted)
	}
	return ctx.Err()
}
