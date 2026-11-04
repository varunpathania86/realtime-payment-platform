resource "random_password" "clickhouse_analytics" {
  count   = var.analytics_enabled ? 1 : 0
  length  = 64
  special = false
}

resource "kubernetes_secret_v1" "clickhouse_analytics" {
  count = var.analytics_enabled ? 1 : 0
  metadata {
    name      = "clickhouse-analytics"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    password = random_password.clickhouse_analytics[0].result
  }
}
