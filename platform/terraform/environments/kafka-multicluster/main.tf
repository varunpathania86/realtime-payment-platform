terraform {
  required_version = ">= 1.16.0, < 2.0.0"
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "4.1.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.7.2"
    }
  }
  backend "local" {}
}

variable "full_stack" {
  type    = bool
  default = false
}

variable "cluster_sizes" {
  description = "Per Kubernetes cluster (1/2/3) workload counts, independent of Minikube. Omitted entries use one."
  type = map(object({
    kafka_brokers      = optional(number, 1)
    postgres_instances = optional(number, 1)
    applications       = optional(map(number), {})
    relay              = optional(number, 1)
    consumer           = optional(number, 1)
    registry           = optional(number, 1)
    ingress            = optional(number, 1)
  }))
  default = {}
  validation {
    condition = alltrue([for key, size in var.cluster_sizes :
      contains(["1", "2", "3"], key) &&
      size.kafka_brokers >= 1 && size.kafka_brokers <= 32 && floor(size.kafka_brokers) == size.kafka_brokers &&
      alltrue([for count in concat([size.postgres_instances, size.relay, size.consumer, size.registry, size.ingress], values(size.applications)) :
      count >= 1 && floor(count) == count])
    ])
    error_message = "Use 1/2/3 keys, positive integer replica counts, and 1-32 Kafka brokers per cluster."
  }
}

variable "database_primary" {
  type    = string
  default = "pg-1"
  validation {
    condition     = contains(["pg-1", "pg-2", "pg-3"], var.database_primary)
    error_message = "Database primary must be pg-1, pg-2 or pg-3."
  }
}
variable "promotion_tokens" {
  type      = map(string)
  default   = {}
  sensitive = true
}
variable "bootstrap_sources" {
  type    = map(string)
  default = {}
}

provider "helm" {
  alias = "cluster_1"
  kubernetes = {
    config_path    = pathexpand(var.kube_config_path)
    config_context = var.profiles[0]
  }
}
provider "helm" {
  alias = "cluster_2"
  kubernetes = {
    config_path    = pathexpand(var.kube_config_path)
    config_context = var.profiles[1]
  }
}
provider "helm" {
  alias = "cluster_3"
  kubernetes = {
    config_path    = pathexpand(var.kube_config_path)
    config_context = var.profiles[2]
  }
}
variable "profiles" {
  type = list(string)
  validation {
    condition     = length(var.profiles) == 3 && length(distinct(var.profiles)) == 3
    error_message = "Exactly three independent profiles are required."
  }
}

variable "addresses" {
  type = list(string)
  validation {
    condition     = length(var.addresses) == 3 && length(distinct(var.addresses)) == 3
    error_message = "Exactly three distinct node addresses are required."
  }
}

variable "kube_config_path" {
  type    = string
  default = "~/.kube/config"
}

provider "kubernetes" {
  alias          = "cluster_1"
  config_path    = pathexpand(var.kube_config_path)
  config_context = var.profiles[0]
}
provider "kubernetes" {
  alias          = "cluster_2"
  config_path    = pathexpand(var.kube_config_path)
  config_context = var.profiles[1]
}
provider "kubernetes" {
  alias          = "cluster_3"
  config_path    = pathexpand(var.kube_config_path)
  config_context = var.profiles[2]
}

locals {
  default_size = {
    kafka_brokers = 1, postgres_instances = 1, applications = {},
    relay         = 1, consumer = 1, registry = 1, ingress = 1
  }
  sizes = { for key in ["1", "2", "3"] : key => lookup(var.cluster_sizes, key, local.default_size) }
  pins = {
    for line in split("\n", trimspace(file("${path.module}/../../../../.tool-versions"))) :
    split(" ", trimspace(line))[0] => split(" ", trimspace(line))[1]
    if trimspace(line) != "" && !startswith(trimspace(line), "#")
  }
  voters = join(",", [for index, address in var.addresses : "${index + 1}@${address}:30093"])
}

resource "random_password" "kafka" {
  length  = 64
  special = false
}
resource "random_id" "cluster" {
  byte_length = 16
}
resource "tls_private_key" "kafka" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}
resource "tls_self_signed_cert" "kafka" {
  private_key_pem       = tls_private_key.kafka.private_key_pem
  validity_period_hours = 8760
  is_ca_certificate     = true
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth", "client_auth", "cert_signing"]
  ip_addresses          = var.addresses
  subject { common_name = "local-multicluster-kafka" }
}

module "cluster_1" {
  source       = "../../modules/kafka-broker"
  providers    = { kubernetes = kubernetes.cluster_1 }
  image        = "apache/kafka:${local.pins["kafka"]}"
  node_id      = 1
  rack         = "1"
  broker_count = local.sizes["1"].kafka_brokers
  address      = var.addresses[0]
  voters       = local.voters
  password     = random_password.kafka.result
  cluster_id   = replace(random_id.cluster.b64_url, "=", "")
  certificate  = tls_self_signed_cert.kafka.cert_pem
  private_key  = tls_private_key.kafka.private_key_pem_pkcs8
}
module "cluster_2" {
  source       = "../../modules/kafka-broker"
  providers    = { kubernetes = kubernetes.cluster_2 }
  image        = "apache/kafka:${local.pins["kafka"]}"
  node_id      = 2
  rack         = "2"
  broker_count = local.sizes["2"].kafka_brokers
  address      = var.addresses[1]
  voters       = local.voters
  password     = random_password.kafka.result
  cluster_id   = replace(random_id.cluster.b64_url, "=", "")
  certificate  = tls_self_signed_cert.kafka.cert_pem
  private_key  = tls_private_key.kafka.private_key_pem_pkcs8
}
module "cluster_3" {
  source       = "../../modules/kafka-broker"
  providers    = { kubernetes = kubernetes.cluster_3 }
  image        = "apache/kafka:${local.pins["kafka"]}"
  node_id      = 3
  rack         = "3"
  broker_count = local.sizes["3"].kafka_brokers
  address      = var.addresses[2]
  voters       = local.voters
  password     = random_password.kafka.result
  cluster_id   = replace(random_id.cluster.b64_url, "=", "")
  certificate  = tls_self_signed_cert.kafka.cert_pem
  private_key  = tls_private_key.kafka.private_key_pem_pkcs8
}

output "application_vars" {
  sensitive = true
  value = {
    external_kafka = {
      brokers = join(",", flatten([for index, key in ["1", "2", "3"] : [
        for broker in range(local.sizes[key].kafka_brokers) :
        "${var.addresses[index]}:${broker == 0 ? 30092 : 30100 + broker}"
      ]]))
      password    = random_password.kafka.result
      certificate = tls_self_signed_cert.kafka.cert_pem
    }

  }
}

output "workload_vars" {
  value = { for key, size in local.sizes : key => {
    application_replicas           = size.applications
    worker_replicas                = { relay = size.relay, consumer = size.consumer }
    registry_replicas              = size.registry
    ingress_replicas               = size.ingress
    telemetry_enabled              = var.observability_enabled
    telemetry_cluster              = var.profiles[index(["1", "2", "3"], key)]
    identity_grafana_client_secret = key == "1" && var.identity_enabled && var.observability_enabled ? random_password.grafana_oidc[0].result : ""
  } }
  sensitive = true
}
