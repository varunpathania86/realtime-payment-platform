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
  }
}

variable "settings" {
  type = object({
    backends        = bool
    cluster         = string
    backendAddress  = string
    images          = map(string)
    grafanaPassword = string
    brokers         = list(string)
  })
  sensitive = true
}
variable "kafka_password" {
  type      = string
  sensitive = true
}
variable "kafka_ca" {
  type = string
}

resource "kubernetes_namespace_v1" "telemetry" {
  metadata { name = "telemetry" }
}
resource "kubernetes_secret_v1" "kafka" {
  metadata {
    name      = "collector-kafka"
    namespace = kubernetes_namespace_v1.telemetry.metadata[0].name
  }
  data = {
    password = var.kafka_password
    "ca.pem" = var.kafka_ca
  }
}
resource "helm_release" "observability" {
  name      = "observability"
  namespace = kubernetes_namespace_v1.telemetry.metadata[0].name
  chart     = "${path.module}/chart"
  timeout   = 600
  wait      = true
  atomic    = true
  values = [yamlencode(merge(var.settings, {
    kafkaRevision = sha256("${var.kafka_password}${var.kafka_ca}")
    sourceRevision = sha256(join("", [
      for filename in sort(fileset("${path.module}/chart", "**")) :
      filesha256("${path.module}/chart/${filename}")
    ]))
  }))]
  depends_on = [kubernetes_secret_v1.kafka]
}
