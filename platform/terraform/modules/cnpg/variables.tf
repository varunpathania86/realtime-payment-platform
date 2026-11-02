terraform {
  required_version = ">= 1.16.0, < 2.0.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
  }
}

variable "name" { type = string }
variable "instances" {
  type    = number
  default = 1
  validation {
    condition     = var.instances >= 1 && floor(var.instances) == var.instances
    error_message = "CNPG instances must be a positive integer."
  }
}
variable "max_connections" {
  type    = number
  default = 128
}
variable "primary" { type = string }
variable "bootstrap_source" { type = string }
variable "promotion_token" {
  type      = string
  sensitive = true
  default   = ""
}
variable "addresses" { type = map(string) }
variable "operator_chart" { type = string }
variable "operator_version" { type = string }
variable "postgres_version" { type = string }
variable "password" {
  type      = string
  sensitive = true
}
variable "ca" { type = string }
variable "server_certificate" { type = string }
variable "server_key" {
  type      = string
  sensitive = true
}
variable "replication_certificate" { type = string }
variable "replication_key" {
  type      = string
  sensitive = true
}
