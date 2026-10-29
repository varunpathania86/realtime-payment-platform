variable "postgres_password" {
  description = "Generated profile-local credential; never commit this or local Terraform state."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.postgres_password))
    error_message = "Use make dev-up/dev-update to generate a 64-character hexadecimal local database password."
  }
}

variable "kube_context" {
  description = "Explicit Minikube profile/context; never use an implicit current context."
  type        = string
  default     = "rpp"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,39}$", var.kube_context))
    error_message = "Use a lowercase Minikube profile, at most 40 characters."
  }
}

variable "kube_config_path" {
  description = "Local kubeconfig file."
  type        = string
  default     = "~/.kube/config"
}

variable "local_images" {
  description = "Catalog module names mapped to locally built, content-tagged images."
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for image in values(var.local_images) : can(regex("^rpp-local/[a-z0-9-]+:dev-[a-f0-9]{16}$", image))])
    error_message = "Local images must use rpp-local/<module>:dev-<16 hex content hash>."
  }
}
