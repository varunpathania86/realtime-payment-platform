package stream

import (
	"testing"

	"github.com/segmentio/kafka-go"
)

func TestReplicationFactor(t *testing.T) {
	k := &Kafka{}
	for _, factor := range []int{1, 3} {
		if err := k.SetReplicationFactor(factor); err != nil || k.replicationFactor != factor {
			t.Fatalf("factor %d: %v", factor, err)
		}
	}
	for _, factor := range []int{-1, 0, 2, 4} {
		if err := k.SetReplicationFactor(factor); err == nil || k.replicationFactor != 3 {
			t.Fatalf("invalid factor %d changed configuration", factor)
		}
	}
}

func TestExistingTopicReplicasMustMatch(t *testing.T) {
	partitions := make([]kafka.Partition, 3)
	for i := range partitions {
		partitions[i].Replicas = make([]kafka.Broker, 3)
	}
	if err := validateReplicas(partitions, 3); err != nil {
		t.Fatal(err)
	}
	if err := validateReplicas(partitions, 1); err == nil {
		t.Fatal("replication downgrade silently accepted")
	}
	partitions[1].Replicas = partitions[1].Replicas[:1]
	if err := validateReplicas(partitions, 3); err == nil {
		t.Fatal("under-replicated assignment silently accepted")
	}
	if err := validateReplicas(nil, 1); err == nil {
		t.Fatal("missing topic silently accepted")
	}
}
