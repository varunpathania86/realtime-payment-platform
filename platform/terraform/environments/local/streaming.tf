variable "streaming_enabled" {
  description = "Opt-in Stage 5 processing, enabled only in the selected member."
  type        = bool
  default     = false
}

variable "streaming_restore_path" {
  description = "Explicit compatible savepoint/checkpoint URI for job submission."
  type        = string
  default     = ""
}

locals {
  runtime_pins = {
    for line in split("\n", file("${local.root}/.tool-versions")) :
    split(" ", trimspace(line))[0] => split(" ", trimspace(line))[1]
    if trimspace(line) != "" && !startswith(trimspace(line), "#")
  }
}

resource "random_password" "streaming" {
  count   = var.streaming_enabled ? 1 : 0
  length  = 64
  special = false
}

resource "kubernetes_secret_v1" "streaming" {
  for_each = var.streaming_enabled ? toset(["apps", "data"]) : toset([])
  metadata {
    name      = "streaming-storage"
    namespace = kubernetes_namespace_v1.foundation[each.key].metadata[0].name
  }
  data = {
    access-key = "rpp-streaming"
    secret-key = random_password.streaming[0].result
  }
}

resource "kubernetes_persistent_volume_claim_v1" "streaming" {
  count = var.streaming_enabled ? 1 : 0
  metadata {
    name      = "minio-state"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = { storage = "5Gi" }
    }
  }
}

resource "kubernetes_service_v1" "minio" {
  count = var.streaming_enabled ? 1 : 0
  metadata {
    name      = "minio"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    selector = { "app.kubernetes.io/name" = "minio" }
    port {
      name        = "http"
      port        = 9000
      target_port = 9000
    }
  }
}

resource "kubernetes_deployment_v1" "minio" {
  count = var.streaming_enabled ? 1 : 0
  metadata {
    name      = "minio"
    namespace = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  }
  spec {
    replicas = 1
    strategy {
      type = "Recreate"
    }
    selector {
      match_labels = { "app.kubernetes.io/name" = "minio" }
    }
    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "minio" }
      }
      spec {
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 10001
          run_as_group    = 10001
          fs_group        = 10001
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }
        container {
          name              = "minio"
          image             = "rpp-runtime/minio:${local.runtime_pins["minio"]}"
          image_pull_policy = "Never"
          args              = ["server", "/data", "--console-address", ":9001"]
          env {
            name = "MINIO_ROOT_USER"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.streaming["data"].metadata[0].name
                key  = "access-key"
              }
            }
          }
          env {
            name = "MINIO_ROOT_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.streaming["data"].metadata[0].name
                key  = "secret-key"
              }
            }
          }
          port {
            name           = "http"
            container_port = 9000
          }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
          readiness_probe {
            http_get {
              path = "/minio/health/ready"
              port = "http"
            }
          }
          liveness_probe {
            http_get {
              path = "/minio/health/live"
              port = "http"
            }
          }
          resources {
            requests = { cpu = "100m", memory = "128Mi" }
            limits   = { cpu = "1", memory = "512Mi" }
          }
          volume_mount {
            name       = "data"
            mount_path = "/data"
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }
        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.streaming[0].metadata[0].name
          }
        }
        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }
}
