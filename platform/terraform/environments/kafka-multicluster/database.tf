locals {
  database_addresses = zipmap(["pg-a", "pg-b", "pg-c"], var.addresses)
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
    for member in ["pg-a", "pg-b", "pg-c"] : [
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

module "database_a" {
  count                   = var.full_stack ? 1 : 0
  source                  = "../../modules/cnpg"
  providers               = { kubernetes = kubernetes.a, helm = helm.a }
  name                    = "pg-a"
  instances               = local.sizes.a.postgres_instances
  max_connections         = local.database_connections
  bootstrap_source        = lookup(var.bootstrap_sources, "pg-a", "")
  promotion_token         = lookup(var.promotion_tokens, "pg-a", "")
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
module "database_b" {
  count                   = var.full_stack ? 1 : 0
  source                  = "../../modules/cnpg"
  providers               = { kubernetes = kubernetes.b, helm = helm.b }
  name                    = "pg-b"
  instances               = local.sizes.b.postgres_instances
  max_connections         = local.database_connections
  bootstrap_source        = lookup(var.bootstrap_sources, "pg-b", "pg-a")
  promotion_token         = lookup(var.promotion_tokens, "pg-b", "")
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
module "database_c" {
  count                   = var.full_stack ? 1 : 0
  source                  = "../../modules/cnpg"
  providers               = { kubernetes = kubernetes.c, helm = helm.c }
  name                    = "pg-c"
  instances               = local.sizes.c.postgres_instances
  max_connections         = local.database_connections
  bootstrap_source        = lookup(var.bootstrap_sources, "pg-c", "pg-a")
  promotion_token         = lookup(var.promotion_tokens, "pg-c", "")
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
