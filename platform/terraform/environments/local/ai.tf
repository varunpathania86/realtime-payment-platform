variable "ai_enabled" {
  description = "Opt-in Stage 10 operations assistant: llama.cpp, knowledge PostgreSQL with pgvector, MCP tools and the orchestrator."
  type        = bool
  default     = false
}

locals {
  # The portal's synthetic sign-in serves both the BI dashboard and the assistant (ADR-025).
  portal_identity_enabled = var.bi_enabled || var.ai_enabled
  # MinIO stores Flink state and, with the assistant, the original knowledge documents.
  object_store_enabled = var.streaming_enabled || var.ai_enabled
  ai_secrets           = toset(["knowledge-admin", "knowledge-writer", "knowledge-reader", "ai-reader"])
}

resource "random_password" "ai" {
  for_each = var.ai_enabled ? local.ai_secrets : toset([])
  length   = 48
  special  = false
  lifecycle {
    # A variable validation would form a cycle with analytics_enabled, which already references ai_enabled.
    precondition {
      condition     = var.analytics_enabled
      error_message = "ai_enabled requires analytics_enabled (the assistant's analytics tools read ClickHouse)."
    }
  }
}

# Signs the portal's assistant assertions (ADR-030). Only web-portal receives the private key.
resource "tls_private_key" "ai_identity" {
  count     = var.ai_enabled ? 1 : 0
  algorithm = "ED25519"
}

resource "kubernetes_secret_v1" "ai_identity" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "ai-identity"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = { "public-key.pem" = tls_private_key.ai_identity[0].public_key_pem }
}

resource "kubernetes_secret_v1" "web_portal_assistant_signer" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "web-portal-assistant-signer"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = { "private-key.pem" = tls_private_key.ai_identity[0].private_key_pem_pkcs8 }
}

# Application credentials: the ingestion job writes as the table owner; the MCP tools read only.
resource "kubernetes_secret_v1" "knowledge_database" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "knowledge-database"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    writer-password = random_password.ai["knowledge-writer"].result
    reader-password = random_password.ai["knowledge-reader"].result
  }
}

# A separate PostgreSQL keeps vector workloads and their extension out of the payments database.
resource "kubernetes_secret_v1" "knowledge_postgres" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "knowledge-postgres"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  data = {
    POSTGRES_USER     = "rpp_knowledge_admin"
    POSTGRES_DB       = "ai_knowledge"
    POSTGRES_PASSWORD = random_password.ai["knowledge-admin"].result
    WRITER_PASSWORD   = random_password.ai["knowledge-writer"].result
    READER_PASSWORD   = random_password.ai["knowledge-reader"].result
  }
}

resource "kubernetes_service_v1" "knowledge_postgres" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "knowledge-postgres"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    selector = { "app.kubernetes.io/name" = "knowledge-postgres" }
    port {
      port        = 5432
      target_port = 5432
    }
  }
}

resource "kubernetes_stateful_set_v1" "knowledge_postgres" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "knowledge-postgres"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    service_name = kubernetes_service_v1.knowledge_postgres[0].metadata[0].name
    replicas     = 1
    selector {
      match_labels = { "app.kubernetes.io/name" = "knowledge-postgres" }
    }
    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "knowledge-postgres" }
      }
      spec {
        automount_service_account_token = false
        security_context {
          # The Debian-based pgvector image runs PostgreSQL as uid/gid 999.
          run_as_non_root = true
          run_as_user     = 999
          run_as_group    = 999
          fs_group        = 999
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name  = "postgres"
          image = "pgvector/pgvector:${local.tool_versions["pgvector"]}"
          env {
            name = "POSTGRES_USER"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.knowledge_postgres[0].metadata[0].name
                key  = "POSTGRES_USER"
              }
            }
          }
          env {
            name = "POSTGRES_DB"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.knowledge_postgres[0].metadata[0].name
                key  = "POSTGRES_DB"
              }
            }
          }
          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.knowledge_postgres[0].metadata[0].name
                key  = "POSTGRES_PASSWORD"
              }
            }
          }
          env {
            name  = "PGDATA"
            value = "/var/lib/postgresql/data/pgdata"
          }
          port { container_port = 5432 }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "100m", memory = "128Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }
          readiness_probe {
            exec { command = ["pg_isready", "-U", "rpp_knowledge_admin", "-d", "ai_knowledge"] }
            period_seconds = 2
          }
          liveness_probe {
            exec { command = ["pg_isready", "-U", "rpp_knowledge_admin", "-d", "ai_knowledge"] }
            initial_delay_seconds = 15
            period_seconds        = 10
          }
          volume_mount {
            name       = "data"
            mount_path = "/var/lib/postgresql/data"
          }
          volume_mount {
            name       = "socket"
            mount_path = "/var/run/postgresql"
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }
        volume {
          name = "socket"
          empty_dir {}
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

resource "kubernetes_config_map_v1" "knowledge_database" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "knowledge-database-init"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  data = {
    # Idempotent: the extension, a table-owning writer and a SELECT-only reader. Tables and the
    # reader's SELECT grant are created by the ingestion job (modules/ai-core sql/knowledge.sql).
    "knowledge.sql" = <<-SQL
      CREATE EXTENSION IF NOT EXISTS vector;
      SELECT 'CREATE ROLE rpp_knowledge_writer LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rpp_knowledge_writer')\gexec
      SELECT 'CREATE ROLE rpp_knowledge_reader LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rpp_knowledge_reader')\gexec
      \getenv writer_password WRITER_PASSWORD
      \getenv reader_password READER_PASSWORD
      ALTER ROLE rpp_knowledge_writer WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'writer_password';
      ALTER ROLE rpp_knowledge_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'reader_password';
      REVOKE ALL ON DATABASE ai_knowledge FROM PUBLIC;
      GRANT CONNECT ON DATABASE ai_knowledge TO rpp_knowledge_writer, rpp_knowledge_reader;
      REVOKE CREATE ON SCHEMA public FROM PUBLIC;
      GRANT USAGE, CREATE ON SCHEMA public TO rpp_knowledge_writer;
      GRANT USAGE ON SCHEMA public TO rpp_knowledge_reader;
      ALTER ROLE rpp_knowledge_reader SET default_transaction_read_only = on;
    SQL
  }
}

resource "kubernetes_job_v1" "knowledge_database" {
  count = var.ai_enabled ? 1 : 0
  metadata {
    name      = "knowledge-database-init"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    backoff_limit           = 6
    active_deadline_seconds = 300
    template {
      metadata {
        annotations = { "sql-checksum" = sha256(kubernetes_config_map_v1.knowledge_database[0].data["knowledge.sql"]) }
      }
      spec {
        restart_policy                  = "Never"
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 999
          run_as_group    = 999
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name    = "psql"
          image   = "pgvector/pgvector:${local.tool_versions["pgvector"]}"
          command = ["psql", "-v", "ON_ERROR_STOP=1", "-h", "knowledge-postgres", "-U", "rpp_knowledge_admin", "-d", "ai_knowledge", "-f", "/sql/knowledge.sql"]
          env {
            name = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.knowledge_postgres[0].metadata[0].name
                key  = "POSTGRES_PASSWORD"
              }
            }
          }
          dynamic "env" {
            for_each = toset(["WRITER_PASSWORD", "READER_PASSWORD"])
            content {
              name = env.value
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.knowledge_postgres[0].metadata[0].name
                  key  = env.value
                }
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
          config_map { name = kubernetes_config_map_v1.knowledge_database[0].metadata[0].name }
        }
      }
    }
  }
  wait_for_completion = true
  timeouts {
    create = "5m"
    update = "5m"
  }
  depends_on = [kubernetes_stateful_set_v1.knowledge_postgres]
  lifecycle {
    # Kubernetes generates the Job selector; without this every plan shows a no-op change.
    ignore_changes = [spec[0].selector]
  }
}
