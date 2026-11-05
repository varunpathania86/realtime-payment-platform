resource "random_password" "realtime_analytics" {
  count   = var.analytics_enabled ? 1 : 0
  length  = 64
  special = false
}

resource "kubernetes_secret_v1" "realtime_analytics" {
  count = var.analytics_enabled ? 1 : 0
  metadata {
    name      = "realtime-analytics"
    namespace = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  }
  data = {
    password = random_password.realtime_analytics[0].result
  }
}
