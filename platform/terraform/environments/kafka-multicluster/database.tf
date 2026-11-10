locals {
  database_addresses = zipmap(["pg-1", "pg-2", "pg-3"], var.addresses)
  operator_chart     = "${path.module}/../../../../.local/tools/cloudnative-pg-${local.pins["cnpg-chart"]}.tgz"
  # Include bounded runtime/migration pools and registry rollout overlap.
  database_connections = max(128, 40 + sum([for size in values(local.sizes) :
    20 * (lookup(size.applications, "payment-api", 1) + size.relay + size.consumer) + 10 * size.registry
  ]))
}

resource "random_password" "database" {
  length  = 64
  special = false
}
resource "tls_private_key" "database_ca" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}
resource "tls_self_signed_cert" "database_ca" {
  private_key_pem       = tls_private_key.database_ca.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 8760
  allowed_uses          = ["cert_signing", "digital_signature", "crl_signing"]
  subject { common_name = "local-distributed-postgres-ca" }
}
resource "tls_private_key" "database_server" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}
resource "tls_cert_request" "database_server" {
  private_key_pem = tls_private_key.database_server.private_key_pem
  ip_addresses    = var.addresses
  dns_names = concat(["payments-postgres", "payments-postgres.data.svc.cluster.local"], flatten([
    for member in ["pg-1", "pg-2", "pg-3"] : [
      for service in ["${member}-rw", "${member}-ro", "${member}-r"] :
      [service, "${service}.postgres", "${service}.postgres.svc", "${service}.postgres.svc.cluster.local"]
    ]
  ]))
  subject { common_name = "local-distributed-postgres" }
}
resource "tls_locally_signed_cert" "database_server" {
  cert_request_pem      = tls_cert_request.database_server.cert_request_pem
  ca_private_key_pem    = tls_private_key.database_ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.database_ca.cert_pem
  validity_period_hours = 8760
  allowed_uses          = ["server_auth", "digital_signature", "key_encipherment"]
}
resource "tls_private_key" "database_replication" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}
resource "tls_cert_request" "database_replication" {
  private_key_pem = tls_private_key.database_replication.private_key_pem
  subject { common_name = "streaming_replica" }
}
resource "tls_locally_signed_cert" "database_replication" {
  cert_request_pem      = tls_cert_request.database_replication.cert_request_pem
  ca_private_key_pem    = tls_private_key.database_ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.database_ca.cert_pem
  validity_period_hours = 8760
  allowed_uses          = ["client_auth", "digital_signature"]
}

module "database_1" {
  count                   = var.full_stack ? 1 : 0
  source                  = "../../modules/cnpg"
  providers               = { kubernetes = kubernetes.cluster_1, helm = helm.cluster_1 }
  name                    = "pg-1"
  instances               = local.sizes["1"].postgres_instances
  max_connections         = local.database_connections
  bootstrap_source        = lookup(var.bootstrap_sources, "pg-1", "")
  promotion_token         = lookup(var.promotion_tokens, "pg-1", "")
  primary                 = var.database_primary
  addresses               = local.database_addresses
  operator_chart          = local.operator_chart
  operator_version        = local.pins["cnpg"]
  postgres_version        = local.pins["cnpg-postgres"]
  password                = random_password.database.result
  ca                      = tls_self_signed_cert.database_ca.cert_pem
  server_certificate      = tls_locally_signed_cert.database_server.cert_pem
  server_key              = tls_private_key.database_server.private_key_pem_pkcs8
  replication_certificate = tls_locally_signed_cert.database_replication.cert_pem
  replication_key         = tls_private_key.database_replication.private_key_pem_pkcs8
}
module "database_2" {
  count                   = var.full_stack ? 1 : 0
  source                  = "../../modules/cnpg"
  providers               = { kubernetes = kubernetes.cluster_2, helm = helm.cluster_2 }
  name                    = "pg-2"
  instances               = local.sizes["2"].postgres_instances
  max_connections         = local.database_connections
  bootstrap_source        = lookup(var.bootstrap_sources, "pg-2", "pg-1")
  promotion_token         = lookup(var.promotion_tokens, "pg-2", "")
  primary                 = var.database_primary
  addresses               = local.database_addresses
  operator_chart          = local.operator_chart
  operator_version        = local.pins["cnpg"]
  postgres_version        = local.pins["cnpg-postgres"]
  password                = random_password.database.result
  ca                      = tls_self_signed_cert.database_ca.cert_pem
  server_certificate      = tls_locally_signed_cert.database_server.cert_pem
  server_key              = tls_private_key.database_server.private_key_pem_pkcs8
  replication_certificate = tls_locally_signed_cert.database_replication.cert_pem
  replication_key         = tls_private_key.database_replication.private_key_pem_pkcs8
}
module "database_3" {
  count                   = var.full_stack ? 1 : 0
  source                  = "../../modules/cnpg"
  providers               = { kubernetes = kubernetes.cluster_3, helm = helm.cluster_3 }
  name                    = "pg-3"
  instances               = local.sizes["3"].postgres_instances
  max_connections         = local.database_connections
  bootstrap_source        = lookup(var.bootstrap_sources, "pg-3", "pg-1")
  promotion_token         = lookup(var.promotion_tokens, "pg-3", "")
  primary                 = var.database_primary
  addresses               = local.database_addresses
  operator_chart          = local.operator_chart
  operator_version        = local.pins["cnpg"]
  postgres_version        = local.pins["cnpg-postgres"]
  password                = random_password.database.result
  ca                      = tls_self_signed_cert.database_ca.cert_pem
  server_certificate      = tls_locally_signed_cert.database_server.cert_pem
  server_key              = tls_private_key.database_server.private_key_pem_pkcs8
  replication_certificate = tls_locally_signed_cert.database_replication.cert_pem
  replication_key         = tls_private_key.database_replication.private_key_pem_pkcs8
}

output "database_application_vars" {
  sensitive = true
  value = var.full_stack ? {
    external_database = {
      address     = local.database_addresses[var.database_primary]
      password    = random_password.database.result
      certificate = tls_self_signed_cert.database_ca.cert_pem
    }
  } : null
}
