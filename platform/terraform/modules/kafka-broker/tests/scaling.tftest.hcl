mock_provider "kubernetes" {}

variables {
  image       = "apache/kafka:test"
  node_id     = 2
  rack        = "b"
  address     = "192.0.2.2"
  voters      = "1@192.0.2.1:30093,2@192.0.2.2:30093,3@192.0.2.3:30093"
  password    = join("", ["synthetic", "-only"])
  cluster_id  = "synthetic-cluster"
  certificate = "synthetic-certificate"
  private_key = "synthetic-key"
}

run "default_preserves_identity" {
  command = plan
  assert {
    condition     = length(kubernetes_stateful_set_v1.kafka) == 1 && kubernetes_stateful_set_v1.kafka["0"].metadata[0].name == "payments-kafka"
    error_message = "The original broker identity must survive the for_each migration."
  }
}

run "two_unique_brokers_one_controller" {
  command = plan
  variables { broker_count = 2 }
  assert {
    condition = (
      length(kubernetes_stateful_set_v1.kafka) == 2 &&
      strcontains(kubernetes_secret_v1.config["0"].data["server.properties"], "node.id=2") &&
      strcontains(kubernetes_secret_v1.config["1"].data["server.properties"], "node.id=5") &&
      strcontains(kubernetes_secret_v1.config["1"].data["server.properties"], "broker.rack=b") &&
      strcontains(kubernetes_secret_v1.config["1"].data["server.properties"], "process.roles=broker\n") &&
      strcontains(kubernetes_secret_v1.config["0"].data["server.properties"], "process.roles=broker,controller")
    )
    error_message = "Each added broker needs a stable unique ID and rack, without adding controller voters."
  }
  assert {
    condition = (
      kubernetes_service_v1.kafka["0"].spec[0].selector["app.kubernetes.io/name"] == "payments-kafka" &&
      kubernetes_service_v1.kafka["1"].spec[0].selector["app.kubernetes.io/name"] == "payments-kafka-1" &&
      strcontains(kubernetes_secret_v1.config["1"].data["server.properties"], "advertised.listeners=SASL_SSL://192.0.2.2:30101")
    )
    error_message = "Every advertised endpoint must select exactly its own broker."
  }
}

run "invalid_broker_count" {
  command = plan
  variables { broker_count = 1.5 }
  expect_failures = [var.broker_count]
}
