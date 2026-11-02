resource "helm_release" "operator" {
  name             = "cnpg"
  namespace        = "cnpg-system"
  create_namespace = true
  chart            = var.operator_chart
  timeout          = 300
  atomic           = true
  values = [yamlencode({
    image = { tag = var.operator_version }
    resources = {
      requests = { cpu = "50m", memory = "64Mi" }
      limits   = { cpu = "500m", memory = "256Mi" }
    }
  })]
}

resource "kubernetes_namespace_v1" "database" {
  metadata { name = "postgres" }
}

resource "kubernetes_secret_v1" "ca" {
  metadata {
    name      = "database-ca"
    namespace = kubernetes_namespace_v1.database.metadata[0].name
  }
  data = { "ca.crt" = var.ca }
}
resource "kubernetes_secret_v1" "server" {
  metadata {
    name      = "database-server"
    namespace = kubernetes_namespace_v1.database.metadata[0].name
  }
  type = "kubernetes.io/tls"
  data = { "tls.crt" = var.server_certificate, "tls.key" = var.server_key }
}
resource "kubernetes_secret_v1" "replication" {
  metadata {
    name      = "database-replication"
    namespace = kubernetes_namespace_v1.database.metadata[0].name
  }
  type = "kubernetes.io/tls"
  data = { "tls.crt" = var.replication_certificate, "tls.key" = var.replication_key }
}
resource "kubernetes_secret_v1" "application" {
  metadata {
    name      = "database-application"
    namespace = kubernetes_namespace_v1.database.metadata[0].name
  }
  type = "kubernetes.io/basic-auth"
  data = { username = "payments", password = var.password }
}
resource "kubernetes_service_v1" "external" {
  metadata {
    name      = "database-external"
    namespace = kubernetes_namespace_v1.database.metadata[0].name
  }
  spec {
    type                        = "NodePort"
    publish_not_ready_addresses = true
    selector                    = { "cnpg.io/cluster" = var.name, "role" = "primary" }
    port {
      port        = 5432
      target_port = 5432
      node_port   = 30432
    }
  }
}

resource "helm_release" "database" {
  name      = "database"
  namespace = kubernetes_namespace_v1.database.metadata[0].name
  chart     = "${path.module}/chart"
  wait      = false
  depends_on = [
    helm_release.operator, kubernetes_secret_v1.ca, kubernetes_secret_v1.server,
    kubernetes_secret_v1.replication, kubernetes_secret_v1.application,
  ]
  values = [yamlencode({
    name            = var.name
    instances       = var.instances
    maxConnections  = var.max_connections
    primary         = var.primary
    bootstrapSource = var.bootstrap_source
    promotionToken  = var.promotion_token
    addresses       = var.addresses
    image           = "ghcr.io/cloudnative-pg/postgresql:${var.postgres_version}"
  })]
}
