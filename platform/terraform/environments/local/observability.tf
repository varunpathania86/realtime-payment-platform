# Reuse the shared observability chart for Stage 11 Grafana SSO without installing
# three-cluster telemetry backends in a resource-constrained single profile.
resource "random_password" "identity_grafana_admin" {
  count   = var.identity_enabled && !var.telemetry_enabled ? 1 : 0
  length  = 48
  special = false
}

module "identity_grafana" {
  count  = var.identity_enabled && !var.telemetry_enabled ? 1 : 0
  source = "../../modules/observability"
  settings = {
    backends         = true
    backendNames     = ["grafana"]
    collectorEnabled = false
    cluster          = var.telemetry_cluster
    backendAddress   = ""
    images           = { grafana = "grafana/grafana:${local.tool_versions["grafana"]}" }
    grafanaPassword  = random_password.identity_grafana_admin[0].result
    brokers          = []
    oidc = {
      enabled      = true
      issuer       = "http://identity.localhost:${var.access_port}/realms/rpp"
      internalUrl  = "http://identity-provider.security.svc.cluster.local:8080/realms/rpp"
      rootUrl      = "http://grafana.localhost:${var.access_port}"
      clientSecret = module.identity[0].grafana_client_secret
    }
  }
  kafka_password = ""
  kafka_ca       = ""
  depends_on     = [module.identity]
}
