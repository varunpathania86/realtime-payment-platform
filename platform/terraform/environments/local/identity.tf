variable "identity_enabled" {
  description = "Opt-in unified Keycloak identity, BFF sessions, payment/AI token verification and application SSO."
  type        = bool
  default     = false
}

variable "identity_grafana_client_secret" {
  description = "Optional OIDC secret owned by the shared multi-cluster Grafana deployment."
  type        = string
  sensitive   = true
  default     = ""
}

module "identity" {
  count                 = var.identity_enabled ? 1 : 0
  source                = "../../modules/identity"
  namespace             = kubernetes_namespace_v1.foundation["security"].metadata[0].name
  database_namespace    = kubernetes_namespace_v1.foundation["data"].metadata[0].name
  image                 = lookup(var.local_images, "identity-provider", "missing:missing")
  postgres_image        = "postgres:${local.tool_versions["postgres"]}"
  access_port           = var.access_port
  grafana_client_secret = var.identity_grafana_client_secret
  depends_on            = [helm_release.ingress]
}
