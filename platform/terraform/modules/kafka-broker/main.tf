locals {
  brokers = {
    for index in range(var.broker_count) : tostring(index) => {
      name    = index == 0 ? "payments-kafka" : "payments-kafka-${index}"
      node_id = var.node_id + index * 3
      port    = index == 0 ? 30092 : 30100 + index
    }
  }
}

resource "kubernetes_namespace_v1" "kafka" {
  metadata { name = "kafka" }
}

resource "kubernetes_secret_v1" "config" {
  for_each = local.brokers
  metadata {
    name      = each.value.name
    namespace = kubernetes_namespace_v1.kafka.metadata[0].name
  }
  data = {
    "server.pem"        = "${var.private_key}${var.certificate}"
    "ca.pem"            = var.certificate
    cluster_id          = var.cluster_id
    password            = var.password
    "client.properties" = <<-CONFIG
      security.protocol=SASL_SSL
      sasl.mechanism=SCRAM-SHA-512
      sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="payments" password="${var.password}";
      ssl.truststore.type=PEM
      ssl.truststore.location=/etc/kafka/tls/ca.pem
      ssl.endpoint.identification.algorithm=https
    CONFIG
    "server.properties" = <<-CONFIG
      process.roles=${each.key == "0" ? "broker,controller" : "broker"}
      node.id=${each.value.node_id}
      broker.rack=${var.rack}
      controller.quorum.voters=${var.voters}
      controller.listener.names=CONTROLLER
      listeners=SASL_SSL://0.0.0.0:9092${each.key == "0" ? ",CONTROLLER://0.0.0.0:9093" : ""}
      advertised.listeners=SASL_SSL://${var.address}:${each.value.port}
      listener.security.protocol.map=SASL_SSL:SASL_SSL,CONTROLLER:SSL
      inter.broker.listener.name=SASL_SSL
      sasl.enabled.mechanisms=SCRAM-SHA-512
      sasl.mechanism.inter.broker.protocol=SCRAM-SHA-512
      listener.name.sasl_ssl.scram-sha-512.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="payments" password="${var.password}";
      ssl.keystore.type=PEM
      ssl.keystore.location=/etc/kafka/tls/server.pem
      ssl.truststore.type=PEM
      ssl.truststore.location=/etc/kafka/tls/ca.pem
      listener.name.controller.ssl.client.auth=required
      ssl.endpoint.identification.algorithm=https
      log.dirs=/var/lib/kafka/data
      num.partitions=3
      default.replication.factor=3
      min.insync.replicas=2
      offsets.topic.replication.factor=3
      transaction.state.log.replication.factor=3
      transaction.state.log.min.isr=2
      auto.create.topics.enable=false
      unclean.leader.election.enable=false
      group.initial.rebalance.delay.ms=0
    CONFIG
  }
}

resource "kubernetes_service_v1" "kafka" {
  for_each = local.brokers
  metadata {
    name      = each.value.name
    namespace = kubernetes_namespace_v1.kafka.metadata[0].name
  }
  spec {
    type                        = "NodePort"
    publish_not_ready_addresses = true
    selector                    = { "app.kubernetes.io/name" = each.value.name }
    port {
      name        = "broker"
      port        = 9092
      target_port = 9092
      node_port   = each.value.port
    }
    dynamic "port" {
      for_each = each.key == "0" ? [1] : []
      content {
        name        = "controller"
        port        = 9093
        target_port = 9093
        node_port   = 30093
      }
    }
  }
}

resource "kubernetes_stateful_set_v1" "kafka" {
  for_each = local.brokers
  metadata {
    name      = each.value.name
    namespace = kubernetes_namespace_v1.kafka.metadata[0].name
  }
  spec {
    service_name = kubernetes_service_v1.kafka[each.key].metadata[0].name
    replicas     = 1
    selector {
      match_labels = { "app.kubernetes.io/name" = each.value.name }
    }
    template {
      metadata {
        labels = { "app.kubernetes.io/name" = each.value.name }
        annotations = {
          configuration = nonsensitive(sha256(kubernetes_secret_v1.config[each.key].data["server.properties"]))
        }
      }
      spec {
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          run_as_group    = 1000
          fs_group        = 1000
          seccomp_profile { type = "RuntimeDefault" }
        }
        init_container {
          name    = "format"
          image   = var.image
          command = ["/bin/bash", "-ec"]
          args    = ["/opt/kafka/bin/kafka-storage.sh format --ignore-formatted --cluster-id \"$CLUSTER_ID\" --config /etc/kafka/tls/server.properties --add-scram \"SCRAM-SHA-512=[name=payments,password=$KAFKA_PASSWORD]\""]
          env {
            name  = "KAFKA_HEAP_OPTS"
            value = "-Xms64m -Xmx128m"
          }
          env {
            name = "CLUSTER_ID"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.config[each.key].metadata[0].name
                key  = "cluster_id"
              }
            }
          }
          env {
            name = "KAFKA_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.config[each.key].metadata[0].name
                key  = "password"
              }
            }
          }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "50m", memory = "128Mi" }
            limits   = { cpu = "500m", memory = "256Mi" }
          }
          volume_mount {
            name       = "data"
            mount_path = "/var/lib/kafka/data"
          }
          volume_mount {
            name       = "config"
            mount_path = "/etc/kafka/tls"
            read_only  = true
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }
        container {
          name    = "kafka"
          image   = var.image
          command = ["/opt/kafka/bin/kafka-server-start.sh", "/etc/kafka/tls/server.properties"]
          env {
            name  = "KAFKA_HEAP_OPTS"
            value = "-Xms128m -Xmx256m"
          }
          env {
            name  = "LOG_DIR"
            value = "/tmp/logs"
          }
          port {
            name           = "broker"
            container_port = 9092
          }
          port {
            name           = "controller"
            container_port = 9093
          }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "100m", memory = "384Mi" }
            limits   = { cpu = "1", memory = "512Mi" }
          }
          startup_probe {
            tcp_socket { port = 9092 }
            period_seconds    = 5
            failure_threshold = 60
          }
          readiness_probe {
            tcp_socket { port = 9092 }
            period_seconds = 5
          }
          liveness_probe {
            tcp_socket { port = 9092 }
            period_seconds    = 10
            failure_threshold = 6
          }
          volume_mount {
            name       = "data"
            mount_path = "/var/lib/kafka/data"
          }
          volume_mount {
            name       = "config"
            mount_path = "/etc/kafka/tls"
            read_only  = true
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }
        volume {
          name = "config"
          secret { secret_name = kubernetes_secret_v1.config[each.key].metadata[0].name }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
    volume_claim_template {
      metadata { name = "data" }
      spec {
        access_modes = ["ReadWriteOnce"]
        resources { requests = { storage = "1Gi" } }
      }
    }
  }
  timeouts {
    create = "10m"
    update = "10m"
  }
}

moved {
  from = kubernetes_secret_v1.config
  to   = kubernetes_secret_v1.config["0"]
}
moved {
  from = kubernetes_service_v1.kafka
  to   = kubernetes_service_v1.kafka["0"]
}
moved {
  from = kubernetes_stateful_set_v1.kafka
  to   = kubernetes_stateful_set_v1.kafka["0"]
}
