terraform {
  required_version = ">= 1.16.0, < 2.0.0"
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.7.2"
    }
  }
}

variable "namespace" { type = string }
variable "database_namespace" { type = string }
variable "image" { type = string }
variable "postgres_image" { type = string }
variable "access_port" { type = number }

locals {
  credential_names = toset([
    "database", "bootstrap-admin", "portal-client", "grafana-client", "superset-client", "argocd-client",
    "ops-viewer", "payments-clerk", "ops-engineer",
  ])
  identity_hostname = "http://identity.localhost:${var.access_port}"
}

resource "random_password" "credential" {
  for_each = local.credential_names
  length   = 48
  special  = false
}

resource "kubernetes_secret_v1" "database" {
  metadata {
    name      = "identity-postgres"
    namespace = var.database_namespace
  }
  data = {
    POSTGRES_USER     = "keycloak"
    POSTGRES_DB       = "keycloak"
    POSTGRES_PASSWORD = random_password.credential["database"].result
  }
}

resource "kubernetes_service_v1" "database" {
  metadata {
    name      = "identity-postgres"
    namespace = var.database_namespace
  }
  spec {
    selector = { "app.kubernetes.io/name" = "identity-postgres" }
    port {
      port        = 5432
      target_port = 5432
    }
  }
}

resource "kubernetes_stateful_set_v1" "database" {
  metadata {
    name      = "identity-postgres"
    namespace = var.database_namespace
  }
  spec {
    service_name = kubernetes_service_v1.database.metadata[0].name
    replicas     = 1
    selector {
      match_labels = { "app.kubernetes.io/name" = "identity-postgres" }
    }
    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "identity-postgres" }
      }
      spec {
        automount_service_account_token = false
        security_context {
          # The pinned Alpine PostgreSQL image uses uid/gid 70.
          run_as_non_root = true
          run_as_user     = 70
          run_as_group    = 70
          fs_group        = 70
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name  = "postgres"
          image = var.postgres_image
          env_from {
            secret_ref { name = kubernetes_secret_v1.database.metadata[0].name }
          }
          env {
            name  = "PGDATA"
            value = "/var/lib/postgresql/data/pgdata"
          }
          port { container_port = 5432 }
          readiness_probe {
            exec { command = ["pg_isready", "-U", "keycloak", "-d", "keycloak"] }
            initial_delay_seconds = 5
            period_seconds        = 5
          }
          liveness_probe {
            exec { command = ["pg_isready", "-U", "keycloak", "-d", "keycloak"] }
            initial_delay_seconds = 30
            period_seconds        = 10
          }
          resources {
            requests = { cpu = "100m", memory = "128Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }
          security_context {
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
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
}

resource "kubernetes_secret_v1" "provider" {
  metadata {
    name      = "identity-provider"
    namespace = var.namespace
  }
  data = {
    KC_DB                       = "postgres"
    KC_DB_URL_HOST              = "identity-postgres.${var.database_namespace}.svc.cluster.local"
    KC_DB_URL_DATABASE          = "keycloak"
    KC_DB_USERNAME              = "keycloak"
    KC_DB_PASSWORD              = random_password.credential["database"].result
    KC_BOOTSTRAP_ADMIN_PASSWORD = random_password.credential["bootstrap-admin"].result
    PORTAL_CLIENT_SECRET        = random_password.credential["portal-client"].result
    GRAFANA_CLIENT_SECRET       = var.grafana_client_secret != "" ? var.grafana_client_secret : random_password.credential["grafana-client"].result
    SUPERSET_CLIENT_SECRET      = random_password.credential["superset-client"].result
    ARGOCD_CLIENT_SECRET        = random_password.credential["argocd-client"].result
    VIEWER_PASSWORD             = random_password.credential["ops-viewer"].result
    CLERK_PASSWORD              = random_password.credential["payments-clerk"].result
    ENGINEER_PASSWORD           = random_password.credential["ops-engineer"].result
    PORTAL_ORIGIN               = "http://localhost:${var.access_port}"
    GRAFANA_ORIGIN              = "http://grafana.localhost:${var.access_port}"
    SUPERSET_ORIGIN             = "http://bi.localhost:${var.access_port}"
    # Reserved disabled client; this stage does not deploy Argo CD.
    ARGOCD_ORIGIN = "http://argocd.localhost:${var.access_port}"
  }
}

resource "kubernetes_secret_v1" "realm" {
  metadata {
    name      = "identity-realm"
    namespace = var.namespace
  }
  data = { "rpp-realm.json" = file("${path.module}/realm.json") }
}

resource "helm_release" "provider" {
  name      = "identity-provider"
  namespace = var.namespace
  chart     = "${path.module}/chart"
  timeout   = 600
  wait      = true
  atomic    = true
  values = [yamlencode({
    image         = var.image
    hostname      = local.identity_hostname
    realmRevision = sha256(file("${path.module}/realm.json"))
    sourceRevision = sha256(join("", [
      for filename in sort(fileset("${path.module}/chart", "**")) :
      filesha256("${path.module}/chart/${filename}")
    ]))
  })]
  depends_on = [kubernetes_stateful_set_v1.database, kubernetes_secret_v1.provider, kubernetes_secret_v1.realm]
}
output "portal_client_secret" {
  value     = random_password.credential["portal-client"].result
  sensitive = true
}

output "grafana_client_secret" {
  value     = var.grafana_client_secret != "" ? var.grafana_client_secret : random_password.credential["grafana-client"].result
  sensitive = true
}

variable "grafana_client_secret" {
  type      = string
  sensitive = true
  default   = ""
}

output "superset_client_secret" {
  value     = random_password.credential["superset-client"].result
  sensitive = true
}
