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
  # The SELECT-only Superset reader password (Stage 9) is added only when BI is enabled.
  data = merge(
    { password = random_password.realtime_analytics[0].result },
    var.bi_enabled ? { bi-reader-password = random_password.bi["bi-reader-password"].result } : {},
  )
}
