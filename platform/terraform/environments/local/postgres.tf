locals {
  tool_versions = {
    for line in split("\n", trimspace(file("${local.root}/.tool-versions"))) :
    split(" ", trimspace(line))[0] => split(" ", trimspace(line))[1]
    if trimspace(line) != "" && !startswith(trimspace(line), "#")
  }
}

resource "kubernetes_secret_v1" "postgres" {
  metadata {
    name      = "payments-postgres"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  data = {
    POSTGRES_USER     = "payments"
    POSTGRES_DB       = "payments"
    POSTGRES_PASSWORD = var.postgres_password
  }
}

resource "kubernetes_secret_v1" "payment_database" {
  metadata {
    name      = "payment-api-database"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    DATABASE_URL = "postgres://payments:${var.postgres_password}@payments-postgres.data.svc.cluster.local:5432/payments?sslmode=disable&connect_timeout=2"
  }
}

resource "kubernetes_service_v1" "postgres" {
  metadata {
    name      = "payments-postgres"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    selector = { "app.kubernetes.io/name" = "payments-postgres" }
    port {
      port        = 5432
      target_port = 5432
    }
  }
}

resource "kubernetes_stateful_set_v1" "postgres" {
  metadata {
    name      = "payments-postgres"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    service_name = kubernetes_service_v1.postgres.metadata[0].name
    replicas     = 1
    selector {
      match_labels = { "app.kubernetes.io/name" = "payments-postgres" }
    }
    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "payments-postgres" }
      }
      spec {
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 70
          run_as_group    = 70
          fs_group        = 70
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name  = "postgres"
          image = "postgres:${local.tool_versions["postgres"]}"
          env_from {
            secret_ref { name = kubernetes_secret_v1.postgres.metadata[0].name }
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
            exec { command = ["pg_isready", "-U", "payments", "-d", "payments"] }
            period_seconds = 2
          }
          liveness_probe {
            exec { command = ["pg_isready", "-U", "payments", "-d", "payments"] }
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
