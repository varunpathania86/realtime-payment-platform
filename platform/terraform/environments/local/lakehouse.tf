locals {
  lakehouse_principals = toset(["root", "writer", "reader", "maintainer"])
}

resource "random_password" "polaris_database" {
  count   = var.lakehouse_enabled ? 1 : 0
  length  = 48
  special = false
}

resource "random_id" "lakehouse_client" {
  for_each    = var.lakehouse_enabled ? local.lakehouse_principals : toset([])
  byte_length = 8
}

resource "random_password" "lakehouse_client" {
  for_each = var.lakehouse_enabled ? local.lakehouse_principals : toset([])
  length   = 48
  special  = false
}

# Signs Polaris OAuth tokens so they remain valid across server restarts.
resource "random_password" "lakehouse_token" {
  count   = var.lakehouse_enabled ? 1 : 0
  length  = 64
  special = false
}

resource "kubernetes_secret_v1" "polaris_database" {
  for_each = var.lakehouse_enabled ? toset(["apps", "data"]) : toset([])
  metadata {
    name      = "polaris-database"
    namespace = kubernetes_namespace_v1.foundation[each.key].metadata[0].name
  }
  data = {
    username = "polaris"
    password = random_password.polaris_database[0].result
  }
}

resource "kubernetes_secret_v1" "lakehouse_catalog" {
  count = var.lakehouse_enabled ? 1 : 0
  metadata {
    name      = "lakehouse-catalog"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = merge(
    { token-secret = random_password.lakehouse_token[0].result },
    { for name in local.lakehouse_principals : "${name}-client-id" => random_id.lakehouse_client[name].hex },
    { for name in local.lakehouse_principals : "${name}-client-secret" => random_password.lakehouse_client[name].result },
  )
}

# Only the Flink submit client receives the least-privilege writer credential.
resource "kubernetes_secret_v1" "lakehouse_writer" {
  count = var.lakehouse_enabled ? 1 : 0
  metadata {
    name      = "lakehouse-writer"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    client-id     = random_id.lakehouse_client["writer"].hex
    client-secret = random_password.lakehouse_client["writer"].result
  }
}

resource "kubernetes_secret_v1" "lakehouse_query" {
  count = var.lakehouse_enabled ? 1 : 0
  metadata {
    name      = "lakehouse-query"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    reader-credential     = "${random_id.lakehouse_client["reader"].hex}:${random_password.lakehouse_client["reader"].result}"
    maintainer-credential = "${random_id.lakehouse_client["maintainer"].hex}:${random_password.lakehouse_client["maintainer"].result}"
  }
}

resource "kubernetes_config_map_v1" "polaris_database" {
  count = var.lakehouse_enabled ? 1 : 0
  metadata {
    name      = "polaris-database-init"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  data = {
    # Idempotent: a dedicated role, database and schema, re-applying the profile password each run.
    "polaris.sql" = <<-SQL
      SELECT 'CREATE ROLE polaris LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'polaris')\gexec
      \getenv polaris_password POLARIS_PASSWORD
      ALTER ROLE polaris WITH LOGIN PASSWORD :'polaris_password';
      SELECT 'CREATE DATABASE polaris OWNER polaris' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'polaris')\gexec
      REVOKE ALL ON DATABASE polaris FROM PUBLIC;
      \connect polaris
      CREATE SCHEMA IF NOT EXISTS polaris_schema AUTHORIZATION polaris;
    SQL
  }
}

resource "kubernetes_job_v1" "polaris_database" {
  count = var.lakehouse_enabled ? 1 : 0
  metadata {
    name      = "polaris-database-init"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    backoff_limit           = 6
    active_deadline_seconds = 300
    template {
      metadata {
        annotations = { "sql-checksum" = sha256(kubernetes_config_map_v1.polaris_database[0].data["polaris.sql"]) }
      }
      spec {
        restart_policy                  = "Never"
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 70
          run_as_group    = 70
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name    = "psql"
          image   = "postgres:${local.tool_versions["postgres"]}"
          command = ["psql", "-v", "ON_ERROR_STOP=1", "-h", "payments-postgres", "-U", "payments", "-d", "payments", "-f", "/sql/polaris.sql"]
          env {
            name = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.postgres.metadata[0].name
                key  = "POSTGRES_PASSWORD"
              }
            }
          }
          env {
            name = "POLARIS_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.polaris_database["data"].metadata[0].name
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
            requests = { cpu = "50m", memory = "32Mi" }
            limits   = { cpu = "250m", memory = "128Mi" }
          }
          volume_mount {
            name       = "sql"
            mount_path = "/sql"
            read_only  = true
          }
        }
        volume {
          name = "sql"
          config_map { name = kubernetes_config_map_v1.polaris_database[0].metadata[0].name }
        }
      }
    }
  }
  wait_for_completion = true
  timeouts {
    create = "5m"
    update = "5m"
  }
  depends_on = [kubernetes_stateful_set_v1.postgres]
  lifecycle {
    # Kubernetes generates the Job selector; without this every plan shows a no-op change.
    ignore_changes = [spec[0].selector]
  }
}

resource "helm_release" "lakehouse_catalog" {
  count           = var.lakehouse_enabled ? 1 : 0
  name            = "lakehouse-catalog"
  namespace       = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  chart           = "${local.root}/modules/lakehouse-catalog/chart"
  timeout         = 600
  wait            = true
  atomic          = true
  cleanup_on_fail = true
  depends_on = [
    kubernetes_job_v1.polaris_database, kubernetes_secret_v1.polaris_database, kubernetes_secret_v1.lakehouse_catalog,
    kubernetes_secret_v1.streaming, kubernetes_deployment_v1.minio,
  ]

  values = [yamlencode({
    image = {
      repository = split(":", lookup(var.local_images, "lakehouse-catalog", "missing:missing"))[0]
      tag        = split(":", lookup(var.local_images, "lakehouse-catalog", "missing:missing"))[1]
      pullPolicy = "Never"
      digest     = ""
    }
  })]

  lifecycle {
    precondition {
      condition     = contains(keys(var.local_images), "lakehouse-catalog")
      error_message = "Missing local Polaris image. Run make dev-images before planning or deploying the lakehouse."
    }
    precondition {
      condition     = nonsensitive(var.external_database == null)
      error_message = "The lakehouse stores Polaris metadata in the profile-local PostgreSQL; it is not supported with external_database."
    }
    precondition {
      condition     = !contains(keys(var.application_replicas), "lakehouse-catalog")
      error_message = "Polaris runs one replica in the local lakehouse and cannot use application_replicas."
    }
  }
}
