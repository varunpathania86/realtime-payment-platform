locals {
  root    = "${path.module}/../../../.."
  catalog = yamldecode(file("${local.root}/solution.yaml"))
  applications = {
    for name in local.catalog.modules : name => "${local.root}/modules/${name}/chart"
    if fileexists("${local.root}/modules/${name}/Dockerfile") && fileexists("${local.root}/modules/${name}/chart/Chart.yaml")
  }
}

resource "kubernetes_namespace_v1" "foundation" {
  for_each = toset(["platform", "data", "apps", "observability", "security", "ai"])
  metadata {
    name = each.value
  }
}

resource "helm_release" "ingress" {
  name            = "traefik"
  namespace       = kubernetes_namespace_v1.foundation["platform"].metadata[0].name
  repository      = "https://traefik.github.io/charts"
  chart           = "traefik"
  version         = "42.1.3"
  timeout         = 300
  wait            = true
  atomic          = true
  cleanup_on_fail = true

  values = [yamlencode({
    deployment = {
      replicas = 1
    }
    service = {
      spec = { type = "ClusterIP" }
    }
    providers = {
      kubernetesCRD = { enabled = false }
      kubernetesIngress = {
        enabled          = true
        publishedService = { enabled = false }
      }
    }
    ingressClass = { enabled = true, isDefaultClass = false, name = "traefik" }
    resources = {
      requests = { cpu = "100m", memory = "64Mi" }
      limits   = { cpu = "500m", memory = "256Mi" }
    }
  })]
}

resource "helm_release" "application" {
  for_each        = local.applications
  name            = each.key
  namespace       = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  chart           = each.value
  timeout         = 180
  wait            = true
  atomic          = true
  cleanup_on_fail = true
  depends_on      = [helm_release.ingress, kubernetes_stateful_set_v1.postgres, kubernetes_secret_v1.payment_database]

  values = [yamlencode({
    image = {
      repository = split(":", lookup(var.local_images, each.key, "missing:missing"))[0]
      tag        = split(":", lookup(var.local_images, each.key, "missing:missing"))[1]
      pullPolicy = "Never"
      digest     = ""
    }
  })]
  lifecycle {
    precondition {
      condition     = contains(keys(var.local_images), each.key)
      error_message = "Missing local image. Run make dev-images before planning or deploying."
    }
  }
}
