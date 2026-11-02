terraform {
  required_version = ">= 1.16.0, < 2.0.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
  }
}

variable "image" {
  type = string
}

variable "node_id" {
  type = number
}

variable "broker_count" {
  type    = number
  default = 1
  validation {
    condition     = var.broker_count >= 1 && var.broker_count <= 32 && floor(var.broker_count) == var.broker_count
    error_message = "Use an integer broker_count from 1 to 32; removing brokers requires explicit partition reassignment first."
  }
}

variable "rack" {
  description = "Kubernetes cluster failure domain, independent of its distribution."
  type        = string
}

variable "address" {
  type = string
}

variable "voters" {
  type = string
}

variable "password" {
  type      = string
  sensitive = true
}

variable "cluster_id" {
  type = string
}

variable "certificate" {
  type = string
}

variable "private_key" {
  type      = string
  sensitive = true
}
