variable "bi_enabled" {
  description = "Opt-in Stage 9 Superset BI embedded in the web portal through the local development identity."
  type        = bool
  default     = false
  validation {
    condition     = !var.bi_enabled || var.analytics_enabled
    error_message = "bi_enabled requires analytics_enabled."
  }
}

variable "access_port" {
  description = "Loopback port used by make dev-access; sets the browser origins for BI embedding."
  type        = number
  default     = 8080
  validation {
    condition     = var.access_port >= 1 && var.access_port <= 65535
    error_message = "access_port must be a TCP port."
  }
}

locals {
  bi_origins    = ["http://localhost:${var.access_port}", "http://127.0.0.1:${var.access_port}"]
  bi_public_url = "http://bi.localhost:${var.access_port}"
  # Equals rpp_bi_assets.EMBEDDED_UUID in modules/bi-dashboards (checked by unit tests).
  bi_dashboard_id = "bfa817e5-b670-5c83-842c-ef5d57da6f8a"
  bi_secrets = toset([
    "secret-key", "guest-token-secret", "database-password", "admin-password", "token-issuer-password",
    "bi-reader-password",
  ])
  portal_secrets = toset(["portal-session-key", "ops-viewer", "payments-clerk", "ops-engineer"])
  # Synthetic users and their permissions; ai:* and knowledge:* matter only when the assistant is enabled.
  portal_users = {
    ops-viewer     = ["bi:view", "ai:invoke", "knowledge:read", "analytics:read"]
    payments-clerk = ["ai:invoke", "payments:read"]
    ops-engineer   = ["bi:view", "ai:invoke", "knowledge:read", "knowledge:restricted", "analytics:read", "payments:read"]
  }
}

resource "random_password" "bi" {
  for_each = var.bi_enabled ? local.bi_secrets : toset([])
  length   = 48
  special  = false
}

resource "kubernetes_secret_v1" "bi_dashboards" {
  count = var.bi_enabled ? 1 : 0
  metadata {
    name      = "bi-dashboards"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    for key in ["secret-key", "guest-token-secret", "database-password", "admin-password", "token-issuer-password"] :
    key => random_password.bi[key].result
  }
}

resource "random_password" "portal" {
  for_each = local.portal_identity_enabled ? local.portal_secrets : toset([])
  length   = 48
  special  = false
}

# Local development identity (ADR-025): synthetic users read by the web-portal; Stage 11 replaces it.
resource "kubernetes_secret_v1" "web_portal_identity" {
  count = local.portal_identity_enabled ? 1 : 0
  metadata {
    name      = "web-portal-identity"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    session-key = random_password.portal["portal-session-key"].result
    dev-users = jsonencode({
      for name, permissions in local.portal_users :
      name => { password = random_password.portal[name].result, permissions = permissions }
    })
  }
}

resource "kubernetes_secret_v1" "superset_database" {
  count = var.bi_enabled ? 1 : 0
  metadata {
    name      = "superset-database"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  data = {
    password = random_password.bi["database-password"].result
  }
}

resource "kubernetes_config_map_v1" "superset_database" {
  count = var.bi_enabled ? 1 : 0
  metadata {
    name      = "superset-database-init"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  data = {
    # Idempotent: a dedicated role and database, re-applying the profile password each run.
    "superset.sql" = <<-SQL
      SELECT 'CREATE ROLE superset LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'superset')\gexec
      \getenv superset_password SUPERSET_PASSWORD
      ALTER ROLE superset WITH LOGIN PASSWORD :'superset_password';
      SELECT 'CREATE DATABASE superset OWNER superset' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'superset')\gexec
      REVOKE ALL ON DATABASE superset FROM PUBLIC;
    SQL
  }
}

resource "kubernetes_job_v1" "superset_database" {
  count = var.bi_enabled ? 1 : 0
  metadata {
    name      = "superset-database-init"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    backoff_limit           = 6
    active_deadline_seconds = 300
    template {
      metadata {
        annotations = { "sql-checksum" = sha256(kubernetes_config_map_v1.superset_database[0].data["superset.sql"]) }
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
          command = ["psql", "-v", "ON_ERROR_STOP=1", "-h", "payments-postgres", "-U", "payments", "-d", "payments", "-f", "/sql/superset.sql"]
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
            name = "SUPERSET_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.superset_database[0].metadata[0].name
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
          config_map { name = kubernetes_config_map_v1.superset_database[0].metadata[0].name }
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

# Installed after the generic releases: importing the Trino connection needs a reachable lakehouse-query.
resource "helm_release" "bi_dashboards" {
  count           = var.bi_enabled ? 1 : 0
  name            = "bi-dashboards"
  namespace       = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  chart           = "${local.root}/modules/bi-dashboards/chart"
  timeout         = 900
  wait            = true
  atomic          = true
  cleanup_on_fail = true
  depends_on = [
    kubernetes_job_v1.superset_database, kubernetes_secret_v1.bi_dashboards, kubernetes_secret_v1.realtime_analytics,
    helm_release.realtime_analytics, helm_release.application,
  ]

  values = [yamlencode({
    image = {
      repository = split(":", lookup(var.local_images, "bi-dashboards", "missing:missing"))[0]
      tag        = split(":", lookup(var.local_images, "bi-dashboards", "missing:missing"))[1]
      pullPolicy = "Never"
      digest     = ""
    }
    trino   = { uri = var.lakehouse_enabled ? "trino://superset@lakehouse-query:8080/lakehouse" : "" }
    embed   = { origins = local.bi_origins }
    ingress = { host = "bi.localhost" }
  })]

  lifecycle {
    precondition {
      condition     = contains(keys(var.local_images), "bi-dashboards")
      error_message = "Missing local Superset image. Run make dev-images before planning or deploying BI."
    }
    precondition {
      condition     = nonsensitive(var.external_database == null)
      error_message = "BI stores Superset metadata in the profile-local PostgreSQL; it is not supported with external_database."
    }
    precondition {
      condition     = !contains(keys(var.application_replicas), "bi-dashboards")
      error_message = "Superset runs one replica without a shared cache and cannot use application_replicas."
    }
  }
}
