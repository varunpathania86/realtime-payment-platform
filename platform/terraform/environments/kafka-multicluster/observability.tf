variable "observability_enabled" {
  type    = bool
  default = false
}
variable "identity_enabled" {
  type    = bool
  default = false
}
variable "access_port" {
  type    = number
  default = 8080
}
resource "random_password" "grafana_oidc" {
  count   = var.identity_enabled && var.observability_enabled ? 1 : 0
  length  = 48
  special = false
}
resource "random_password" "grafana" {
  length  = 32
  special = false
}
locals {
  telemetry_images = {
    otelcol      = "otel/opentelemetry-collector-contrib:${local.pins["otelcol"]}"
    prometheus   = "prom/prometheus:v${local.pins["prometheus"]}"
    alertmanager = "prom/alertmanager:v${local.pins["alertmanager"]}"
    loki         = "grafana/loki:${local.pins["loki"]}"
    tempo        = "grafana/tempo:${local.pins["tempo"]}"
    grafana      = "grafana/grafana:${local.pins["grafana"]}"
  }
  telemetry_settings = {
    for index, key in ["1", "2", "3"] : key => {
      backends        = key == "1"
      cluster         = var.profiles[index]
      backendAddress  = var.addresses[0]
      images          = local.telemetry_images
      grafanaPassword = key == "1" ? random_password.grafana.result : ""
      brokers         = [for address in var.addresses : "${address}:30092"]
      oidc = {
        enabled      = key == "1" && var.identity_enabled
        issuer       = "http://identity.localhost:${var.access_port}/realms/rpp"
        internalUrl  = "http://identity-provider.security.svc.cluster.local:8080/realms/rpp"
        rootUrl      = "http://grafana.localhost:${var.access_port}"
        clientSecret = var.identity_enabled && var.observability_enabled ? random_password.grafana_oidc[0].result : ""
      }
    }
  }
}
module "telemetry_1" {
  count          = var.observability_enabled ? 1 : 0
  source         = "../../modules/observability"
  providers      = { kubernetes = kubernetes.cluster_1, helm = helm.cluster_1 }
  settings       = local.telemetry_settings["1"]
  kafka_password = random_password.kafka.result
  kafka_ca       = tls_self_signed_cert.kafka.cert_pem
  depends_on     = [module.cluster_1, module.cluster_2, module.cluster_3, module.database_1, module.database_2, module.database_3]
}
module "telemetry_2" {
  count          = var.observability_enabled ? 1 : 0
  source         = "../../modules/observability"
  providers      = { kubernetes = kubernetes.cluster_2, helm = helm.cluster_2 }
  settings       = local.telemetry_settings["2"]
  kafka_password = random_password.kafka.result
  kafka_ca       = tls_self_signed_cert.kafka.cert_pem
  depends_on     = [module.cluster_1, module.cluster_2, module.cluster_3, module.database_1, module.database_2, module.database_3]
}
module "telemetry_3" {
  count          = var.observability_enabled ? 1 : 0
  source         = "../../modules/observability"
  providers      = { kubernetes = kubernetes.cluster_3, helm = helm.cluster_3 }
  settings       = local.telemetry_settings["3"]
  kafka_password = random_password.kafka.result
  kafka_ca       = tls_self_signed_cert.kafka.cert_pem
  depends_on     = [module.cluster_1, module.cluster_2, module.cluster_3, module.database_1, module.database_2, module.database_3]
}
