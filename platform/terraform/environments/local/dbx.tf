variable "dbx_enabled" {
  description = "Opt-in DBX Web workbench with in-cluster connection provisioning."
  type        = bool
  default     = false
}

locals {
  dbx_analytics_reader_key = format("%s-reader-password", var.ai_enabled ? "ai" : "bi")
}

resource "random_password" "dbx" {
  count   = var.dbx_enabled ? 1 : 0
  length  = 48
  special = false
}

resource "kubernetes_secret_v1" "dbx_workbench" {
  count = var.dbx_enabled ? 1 : 0
  metadata {
    name      = "dbx-workbench"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    password = random_password.dbx[0].result
  }
}

resource "kubernetes_persistent_volume_claim_v1" "dbx" {
  count = var.dbx_enabled ? 1 : 0
  metadata {
    name      = "dbx-workbench"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = { storage = "1Gi" }
    }
  }
}

resource "kubernetes_config_map_v1" "dbx_provisioner" {
  count = var.dbx_enabled ? 1 : 0
  metadata {
    name      = "dbx-connection-provisioner"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    "dbx_provision.py"       = file("${local.root}/scripts/dbx_provision.py")
    "dbx_runtime_install.py" = file("${local.root}/scripts/dbx_runtime_install.py")
  }
}

resource "kubernetes_service_v1" "dbx" {
  count = var.dbx_enabled ? 1 : 0
  metadata {
    name      = "dbx-workbench"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  spec {
    selector = { "app.kubernetes.io/name" = "dbx-workbench" }
    port {
      name        = "http"
      port        = 4224
      target_port = "http"
    }
  }
}

resource "kubernetes_deployment_v1" "dbx" {
  count = var.dbx_enabled ? 1 : 0
  metadata {
    name      = "dbx-workbench"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
    labels    = { "app.kubernetes.io/name" = "dbx-workbench" }
  }
  spec {
    replicas = 1
    selector {
      match_labels = { "app.kubernetes.io/name" = "dbx-workbench" }
    }
    strategy {
      type = "Recreate"
    }
    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "dbx-workbench" }
      }
      spec {
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 10001
          run_as_group    = 10001
          fs_group        = 10001
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name              = "dbx"
          image             = lookup(var.local_images, "dbx-workbench", "rpp-local/dbx-workbench:missing")
          image_pull_policy = "Never"
          env {
            name = "DBX_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.dbx_workbench[0].metadata[0].name
                key  = "password"
              }
            }
          }
          env {
            name  = "DBX_DISABLE_PASSWORD_VIEWER"
            value = "true"
          }
          env {
            name  = "DBX_BIND_ADDR"
            value = "0.0.0.0"
          }
          env {
            name  = "DBX_DATA_DIR"
            value = "/app/data"
          }
          env {
            name  = "DBX_BACKUP_ROOT"
            value = "/run/dbx-tmp/backups"
          }
          env {
            name  = "DBX_AGENT_DIR"
            value = "/app/data/agents"
          }
          env {
            name  = "TMPDIR"
            value = "/run/dbx-tmp"
          }
          env {
            name  = "JAVA_TOOL_OPTIONS"
            value = "-Djava.io.tmpdir=/run/dbx-tmp"
          }
          port {
            name           = "http"
            container_port = 4224
          }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          startup_probe {
            http_get {
              path = "/api/auth/check"
              port = "http"
            }
            period_seconds    = 5
            failure_threshold = 36
          }
          readiness_probe {
            http_get {
              path = "/api/auth/check"
              port = "http"
            }
            period_seconds = 5
          }
          liveness_probe {
            http_get {
              path = "/api/auth/check"
              port = "http"
            }
            period_seconds    = 20
            failure_threshold = 3
          }
          resources {
            requests = { cpu = "100m", memory = "256Mi" }
            limits   = { cpu = "1", memory = "768Mi" }
          }
          volume_mount {
            name       = "data"
            mount_path = "/app/data"
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/run/dbx-tmp"
          }
          volume_mount {
            name       = "postgres-ca"
            mount_path = "/etc/dbx/postgres"
            read_only  = true
          }
          volume_mount {
            name       = "kafka-ca"
            mount_path = "/etc/dbx/kafka"
            read_only  = true
          }
        }
        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.dbx[0].metadata[0].name
          }
        }
        volume {
          name = "tmp"
          empty_dir { size_limit = "256Mi" }
        }
        volume {
          name = "postgres-ca"
          secret {
            secret_name = kubernetes_secret_v1.payment_database.metadata[0].name
            items {
              key  = "ca.crt"
              path = "ca.crt"
            }
          }
        }
        volume {
          name = "kafka-ca"
          secret {
            secret_name = kubernetes_secret_v1.payment_events.metadata[0].name
            items {
              key  = "ca.pem"
              path = "ca.pem"
            }
          }
        }
      }
    }
  }
  depends_on = [
    helm_release.ingress,
    kubernetes_secret_v1.payment_database,
    kubernetes_secret_v1.payment_events,
  ]
}

resource "terraform_data" "dbx_connection_revision" {
  count = var.dbx_enabled ? 1 : 0
  input = sha256(jsonencode([
    filesha256("${local.root}/scripts/dbx_provision.py"),
    filesha256("${local.root}/scripts/dbx_runtime_install.py"),
    lookup(var.local_images, "dbx-workbench", ""),
    local.tool_versions["dbx-api-contract"],
    kubernetes_secret_v1.dbx_workbench[0].data["password"],
    kubernetes_secret_v1.payment_database.data["DATABASE_URL"],
    kubernetes_secret_v1.payment_events.data["KAFKA_USERNAME"],
    kubernetes_secret_v1.payment_events.data["KAFKA_PASSWORD"],
    var.analytics_enabled ? kubernetes_secret_v1.realtime_analytics[0].data["password"] : "",
    var.lakehouse_enabled,
    var.bi_enabled ? random_password.bi["database-password"].result : "",
    var.lakehouse_enabled ? kubernetes_secret_v1.polaris_database["apps"].data["password"] : "",
    var.ai_enabled ? kubernetes_secret_v1.knowledge_database[0].data["reader-password"] : "",
    var.ai_enabled ? random_password.ai["ai-reader"].result : (var.bi_enabled ? random_password.bi["bi-reader-password"].result : ""),
  ]))
}

resource "kubernetes_job_v1" "dbx_connections" {
  count = var.dbx_enabled ? 1 : 0
  metadata {
    name      = "dbx-connection-sync"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  spec {
    backoff_limit           = 2
    active_deadline_seconds = 600
    template {
      metadata {
        annotations = {
          "rpp.dev/adapter-revision" = filesha256("${local.root}/scripts/dbx_provision.py")
          "rpp.dev/api-contract"     = local.tool_versions["dbx-api-contract"]
          "rpp.dev/connection-revision" = nonsensitive(sha256(jsonencode([
            kubernetes_secret_v1.payment_database.data["DATABASE_URL"],
            kubernetes_secret_v1.payment_events.data["KAFKA_USERNAME"],
            kubernetes_secret_v1.payment_events.data["KAFKA_PASSWORD"],
            var.analytics_enabled ? kubernetes_secret_v1.realtime_analytics[0].data["password"] : "",
            var.lakehouse_enabled ? "lakehouse-query-enabled" : "",
            var.bi_enabled ? random_password.bi["database-password"].result : "",
            var.lakehouse_enabled ? kubernetes_secret_v1.polaris_database["apps"].data["password"] : "",
            var.ai_enabled ? kubernetes_secret_v1.knowledge_database[0].data["reader-password"] : "",
            var.ai_enabled ? random_password.ai["ai-reader"].result : (var.bi_enabled ? random_password.bi["bi-reader-password"].result : ""),
          ])))
        }
      }
      spec {
        restart_policy                  = "Never"
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          run_as_group    = 1000
          fs_group        = 1000
          seccomp_profile { type = "RuntimeDefault" }
        }
        init_container {
          name              = "runtime-package"
          image             = lookup(var.local_images, "dbx-workbench", "rpp-local/dbx-workbench:missing")
          image_pull_policy = "Never"
          command           = ["cp", "/opt/dbx/runtime.zip", "/opt/dbx-runtime/runtime.zip"]
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "25m", memory = "32Mi" }
            limits   = { cpu = "250m", memory = "64Mi" }
          }
          volume_mount {
            name       = "runtime-package"
            mount_path = "/opt/dbx-runtime"
          }
        }
        container {
          name              = "provision"
          image             = "python:${local.tool_versions["ai-python"]}"
          image_pull_policy = "IfNotPresent"
          command           = ["sh", "-ec", "python /opt/dbx/dbx_runtime_install.py && python /opt/dbx/dbx_provision.py && python /opt/dbx/dbx_runtime_install.py verify"]
          env {
            name  = "PYTHONDONTWRITEBYTECODE"
            value = "1"
          }
          env {
            name  = "DBX_API_URL"
            value = "http://dbx-workbench.apps.svc.cluster.local:4224"
          }
          env {
            name  = "DBX_API_CONTRACT_VERSION"
            value = local.tool_versions["dbx-api-contract"]
          }
          env {
            name = "DBX_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.dbx_workbench[0].metadata[0].name
                key  = "password"
              }
            }
          }
          env {
            name = "PAYMENT_DATABASE_URL"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.payment_database.metadata[0].name
                key  = "DATABASE_URL"
              }
            }
          }
          env {
            name  = "TRINO_ENABLED"
            value = var.lakehouse_enabled ? "1" : "0"
          }
          dynamic "env" {
            for_each = var.analytics_enabled ? [1] : []
            content {
              name = "ANALYTICS_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.realtime_analytics[0].metadata[0].name
                  key  = "password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.bi_enabled ? [1] : []
            content {
              name = "BI_DATABASE_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.bi_dashboards[0].metadata[0].name
                  key  = "database-password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.lakehouse_enabled ? [1] : []
            content {
              name = "POLARIS_DATABASE_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.polaris_database["apps"].metadata[0].name
                  key  = "password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.ai_enabled ? [1] : []
            content {
              name = "KNOWLEDGE_READER_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.knowledge_database[0].metadata[0].name
                  key  = "reader-password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.ai_enabled || var.bi_enabled ? [1] : []
            content {
              name = "ANALYTICS_READER_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.realtime_analytics[0].metadata[0].name
                  key  = local.dbx_analytics_reader_key
                }
              }
            }
          }
          env {
            name  = "ANALYTICS_READER_USERNAME"
            value = var.ai_enabled ? "rpp_ai_reader" : (var.bi_enabled ? "rpp_bi_reader" : "")
          }
          env {
            name = "KAFKA_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.payment_events.metadata[0].name
                key  = "KAFKA_PASSWORD"
              }
            }
          }
          env {
            name = "KAFKA_USERNAME"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.payment_events.metadata[0].name
                key  = "KAFKA_USERNAME"
              }
            }
          }
          dynamic "env" {
            for_each = nonsensitive(var.external_kafka == null) ? [] : [1]
            content {
              name  = "KAFKA_BOOTSTRAP_SERVERS"
              value = nonsensitive(var.external_kafka.brokers)
            }
          }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "25m", memory = "48Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }
          volume_mount {
            name       = "provisioner"
            mount_path = "/opt/dbx"
            read_only  = true
          }
          volume_mount {
            name       = "runtime-package"
            mount_path = "/opt/dbx-runtime"
            read_only  = true
          }
        }
        volume {
          name = "provisioner"
          config_map {
            name = kubernetes_config_map_v1.dbx_provisioner[0].metadata[0].name
          }
        }
        volume {
          name = "runtime-package"
          empty_dir { size_limit = "128Mi" }
        }
      }
    }
    ttl_seconds_after_finished = 86400
  }
  wait_for_completion = true
  timeouts {
    create = "5m"
    update = "5m"
  }
  depends_on = [
    kubernetes_deployment_v1.dbx,
    kubernetes_stateful_set_v1.postgres,
    kubernetes_stateful_set_v1.kafka,
    helm_release.realtime_analytics,
    helm_release.lakehouse_catalog,
    helm_release.application,
  ]
  lifecycle {
    ignore_changes       = [spec[0].selector]
    replace_triggered_by = [terraform_data.dbx_connection_revision]
  }
}
