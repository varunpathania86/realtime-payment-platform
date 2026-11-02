resource "random_password" "kafka" {
  count   = nonsensitive(var.external_kafka == null) ? 1 : 0
  length  = 64
  special = false
}

resource "random_id" "kafka_cluster" {
  count       = nonsensitive(var.external_kafka == null) ? 1 : 0
  byte_length = 16
}

resource "tls_private_key" "kafka" {
  count       = nonsensitive(var.external_kafka == null) ? 1 : 0
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "kafka" {
  count                 = nonsensitive(var.external_kafka == null) ? 1 : 0
  private_key_pem       = tls_private_key.kafka[0].private_key_pem
  validity_period_hours = 8760
  is_ca_certificate     = true
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth", "cert_signing"]
  dns_names             = ["payments-kafka", "payments-kafka.data", "payments-kafka.data.svc", "payments-kafka.data.svc.cluster.local"]
  subject {
    common_name = "payments-kafka.data.svc.cluster.local"
  }
}

locals {
  kafka_host       = "payments-kafka.data.svc.cluster.local"
  kafka_properties = <<-CONFIG
    process.roles=broker,controller
    node.id=1
    controller.quorum.voters=1@127.0.0.1:9093
    controller.listener.names=CONTROLLER
    listeners=SASL_SSL://0.0.0.0:9092,CONTROLLER://127.0.0.1:9093
    advertised.listeners=SASL_SSL://${local.kafka_host}:9092
    listener.security.protocol.map=SASL_SSL:SASL_SSL,CONTROLLER:PLAINTEXT
    inter.broker.listener.name=SASL_SSL
    sasl.enabled.mechanisms=SCRAM-SHA-512
    sasl.mechanism.inter.broker.protocol=SCRAM-SHA-512
    listener.name.sasl_ssl.scram-sha-512.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="payments" password="${var.external_kafka == null ? random_password.kafka[0].result : var.external_kafka.password}";
    ssl.keystore.type=PEM
    ssl.keystore.location=/etc/kafka/tls/server.pem
    ssl.truststore.type=PEM
    ssl.truststore.location=/etc/kafka/tls/ca.pem
    ssl.client.auth=none
    log.dirs=/var/lib/kafka/data
    num.partitions=3
    offsets.topic.replication.factor=1
    transaction.state.log.replication.factor=1
    transaction.state.log.min.isr=1
    auto.create.topics.enable=false
    group.initial.rebalance.delay.ms=0
  CONFIG
}

resource "kubernetes_secret_v1" "kafka" {
  count = nonsensitive(var.external_kafka == null) ? 1 : 0
  metadata {
    name      = "payments-kafka"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  data = {
    "server.properties" = local.kafka_properties
    "server.pem"        = "${tls_private_key.kafka[0].private_key_pem_pkcs8}${tls_self_signed_cert.kafka[0].cert_pem}"
    "ca.pem"            = tls_self_signed_cert.kafka[0].cert_pem
    password            = random_password.kafka[0].result
    cluster_id          = replace(random_id.kafka_cluster[0].b64_url, "=", "")
  }
}

resource "kubernetes_secret_v1" "payment_events" {
  metadata {
    name      = "payment-api-events"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    KAFKA_USERNAME = "payments"
    KAFKA_PASSWORD = var.external_kafka == null ? random_password.kafka[0].result : var.external_kafka.password
    "ca.pem"       = var.external_kafka == null ? tls_self_signed_cert.kafka[0].cert_pem : var.external_kafka.certificate
  }
}

resource "kubernetes_config_map_v1" "event_schema" {
  metadata {
    name      = "payment-event-schema"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    "payment-initiated-v1.avsc" = file("${local.root}/modules/event-contracts/schemas/payment-initiated-v1.avsc")
  }
}

resource "kubernetes_service_v1" "kafka" {
  count = nonsensitive(var.external_kafka == null) ? 1 : 0
  metadata {
    name      = "payments-kafka"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    selector = { "app.kubernetes.io/name" = "payments-kafka" }
    port {
      port        = 9092
      target_port = 9092
    }
  }
}

resource "kubernetes_stateful_set_v1" "kafka" {
  count = nonsensitive(var.external_kafka == null) ? 1 : 0
  metadata {
    name      = "payments-kafka"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    service_name = kubernetes_service_v1.kafka[0].metadata[0].name
    replicas     = 1
    selector {
      match_labels = { "app.kubernetes.io/name" = "payments-kafka" }
    }
    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "payments-kafka" }
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
          image   = "apache/kafka:${local.tool_versions["kafka"]}"
          command = ["/bin/bash", "-ec"]
          args    = ["/opt/kafka/bin/kafka-storage.sh format --ignore-formatted --cluster-id \"$CLUSTER_ID\" --config /etc/kafka/tls/server.properties --add-scram \"SCRAM-SHA-512=[name=payments,password=$KAFKA_PASSWORD]\""]
          env {
            name = "CLUSTER_ID"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.kafka[0].metadata[0].name
                key  = "cluster_id"
              }
            }
          }
          env {
            name = "KAFKA_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.kafka[0].metadata[0].name
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
            requests = { cpu = "100m", memory = "128Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
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
          image   = "apache/kafka:${local.tool_versions["kafka"]}"
          command = ["/opt/kafka/bin/kafka-server-start.sh", "/etc/kafka/tls/server.properties"]
          env {
            name  = "KAFKA_HEAP_OPTS"
            value = "-Xms128m -Xmx384m"
          }
          env {
            name  = "LOG_DIR"
            value = "/tmp/logs"
          }
          port { container_port = 9092 }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "200m", memory = "512Mi" }
            limits   = { cpu = "1", memory = "768Mi" }
          }
          startup_probe {
            tcp_socket { port = 9092 }
            period_seconds    = 2
            failure_threshold = 90
          }
          readiness_probe {
            tcp_socket { port = 9092 }
            period_seconds = 5
          }
          liveness_probe {
            tcp_socket { port = 9092 }
            period_seconds = 10
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
          secret { secret_name = kubernetes_secret_v1.kafka[0].metadata[0].name }
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
    create = "5m"
    update = "5m"
  }
}

resource "kubernetes_service_v1" "registry" {
  metadata {
    name      = "apicurio"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    selector = { "app.kubernetes.io/name" = "apicurio" }
    port {
      port        = 8080
      target_port = 8080
    }
  }
}

resource "kubernetes_deployment_v1" "registry" {
  metadata {
    name      = "apicurio"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    replicas = var.registry_replicas
    selector {
      match_labels = { "app.kubernetes.io/name" = "apicurio" }
    }
    template {
      metadata {
        annotations = var.external_database == null ? {} : { "database-configuration" = nonsensitive(sha256(jsonencode(var.external_database))) }
        labels      = { "app.kubernetes.io/name" = "apicurio" }
      }
      spec {
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 1001
          run_as_group    = 1001
          fs_group        = 1001
          seccomp_profile { type = "RuntimeDefault" }
        }
        init_container {
          name    = "schema"
          image   = "postgres:${local.tool_versions["postgres"]}"
          command = ["psql", "-h", local.database_host, "-p", tostring(local.database_port), "-U", "payments", "-d", "payments", "-v", "ON_ERROR_STOP=1", "-c", "CREATE SCHEMA IF NOT EXISTS apicurio AUTHORIZATION payments;"]
          env {
            name  = "PGSSLMODE"
            value = local.database_tls
          }
          env {
            name  = "PGSSLROOTCERT"
            value = "/etc/database/ca.crt"
          }
          volume_mount {
            name       = "database-ca"
            mount_path = "/etc/database"
            read_only  = true
          }
          env {
            name = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.postgres.metadata[0].name
                key  = "POSTGRES_PASSWORD"
              }
            }
          }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "50m", memory = "32Mi" }
            limits   = { cpu = "250m", memory = "64Mi" }
          }
        }
        container {
          name  = "registry"
          image = "apicurio/apicurio-registry:${local.tool_versions["apicurio"]}"
          env {
            name  = "APICURIO_DATASOURCE_JDBC_INITIAL_SIZE"
            value = "1"
          }
          env {
            name  = "APICURIO_DATASOURCE_JDBC_MIN_SIZE"
            value = "1"
          }
          env {
            name  = "APICURIO_DATASOURCE_JDBC_MAX_SIZE"
            value = "5"
          }
          env {
            name  = "APICURIO_STORAGE_KIND"
            value = "sql"
          }
          env {
            name  = "APICURIO_STORAGE_SQL_KIND"
            value = "postgresql"
          }
          env {
            name  = "APICURIO_DATASOURCE_URL"
            value = "jdbc:postgresql://${local.database_host}:${local.database_port}/payments?currentSchema=apicurio&sslmode=${local.database_tls}&sslrootcert=/etc/database/ca.crt"
          }
          env {
            name  = "APICURIO_DATASOURCE_USERNAME"
            value = "payments"
          }
          env {
            name = "APICURIO_DATASOURCE_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.postgres.metadata[0].name
                key  = "POSTGRES_PASSWORD"
              }
            }
          }
          env {
            name  = "JAVA_OPTS_APPEND"
            value = "-Xms64m -Xmx256m -Djava.io.tmpdir=/tmp"
          }
          port { container_port = 8080 }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "100m", memory = "256Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }
          startup_probe {
            http_get {
              path = "/health/ready"
              port = 9000
            }
            period_seconds    = 2
            failure_threshold = 90
          }
          readiness_probe {
            http_get {
              path = "/health/ready"
              port = 9000
            }
          }
          liveness_probe {
            http_get {
              path = "/health/live"
              port = 9000
            }
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
          volume_mount {
            name       = "database-ca"
            mount_path = "/etc/database"
            read_only  = true
          }
        }
        volume {
          name = "database-ca"
          secret {
            secret_name = kubernetes_secret_v1.postgres.metadata[0].name
            items {
              key  = "ca.crt"
              path = "ca.crt"
            }
          }
        }
        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }
  depends_on = [kubernetes_stateful_set_v1.postgres]
  timeouts {
    create = "5m"
    update = "5m"
  }
}
