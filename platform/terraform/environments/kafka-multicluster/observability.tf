variable "observability_enabled" {
  type    = bool
  default = false
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
    for index, key in ["a", "b", "c"] : key => {
      backends        = key == "a"
      cluster         = var.profiles[index]
      backendAddress  = var.addresses[0]
      images          = local.telemetry_images
      grafanaPassword = key == "a" ? random_password.grafana.result : ""
      brokers         = [for address in var.addresses : "${address}:30092"]
    }
  }
}
module "telemetry_a" {
  count          = var.observability_enabled ? 1 : 0
  source         = "../../modules/observability"
  providers      = { kubernetes = kubernetes.a, helm = helm.a }
  settings       = local.telemetry_settings.a
  kafka_password = random_password.kafka.result
  kafka_ca       = tls_self_signed_cert.kafka.cert_pem
  depends_on     = [module.a, module.b, module.c, module.database_a, module.database_b, module.database_c]
}
module "telemetry_b" {
  count          = var.observability_enabled ? 1 : 0
  source         = "../../modules/observability"
  providers      = { kubernetes = kubernetes.b, helm = helm.b }
  settings       = local.telemetry_settings.b
  kafka_password = random_password.kafka.result
  kafka_ca       = tls_self_signed_cert.kafka.cert_pem
  depends_on     = [module.a, module.b, module.c, module.database_a, module.database_b, module.database_c]
}
module "telemetry_c" {
  count          = var.observability_enabled ? 1 : 0
  source         = "../../modules/observability"
  providers      = { kubernetes = kubernetes.c, helm = helm.c }
  settings       = local.telemetry_settings.c
  kafka_password = random_password.kafka.result
  kafka_ca       = tls_self_signed_cert.kafka.cert_pem
  depends_on     = [module.a, module.b, module.c, module.database_a, module.database_b, module.database_c]
}
