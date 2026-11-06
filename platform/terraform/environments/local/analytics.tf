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
  # SELECT-only reader passwords: Superset (Stage 9) with BI, the assistant tools (Stage 10) with AI.
  data = merge(
    { password = random_password.realtime_analytics[0].result },
    var.bi_enabled ? { bi-reader-password = random_password.bi["bi-reader-password"].result } : {},
    var.ai_enabled ? { ai-reader-password = random_password.ai["ai-reader"].result } : {},
  )
}
