output "applications" {
  description = "Locally deployed module names."
  value       = sort(keys(helm_release.application))
}

output "access" {
  description = "Loopback-only ingress access; no hosts-file changes or Minikube tunnel."
  value       = "Run make dev-access${var.kube_context == "rpp" ? "" : " MULTI_CLUSTER=3 MINIKUBE_PROFILE=${var.kube_context}"}, then open http://127.0.0.1:8080"
}
