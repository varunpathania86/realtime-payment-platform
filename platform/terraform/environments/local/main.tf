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
      replicas = var.ingress_replicas
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
  depends_on      = [helm_release.ingress, kubernetes_stateful_set_v1.postgres, kubernetes_secret_v1.payment_database, kubernetes_stateful_set_v1.kafka, kubernetes_deployment_v1.registry, kubernetes_secret_v1.payment_events, kubernetes_config_map_v1.event_schema]

  values = [yamlencode({
    replicaCount = lookup(var.application_replicas, each.key, 1)
    telemetry = {
      enabled  = var.telemetry_enabled
      endpoint = var.telemetry_enabled ? "http://collector.telemetry.svc.cluster.local:4318" : ""
      cluster  = var.telemetry_cluster
    }
    events = {
      relayReplicas     = var.worker_replicas.relay
      consumerReplicas  = var.worker_replicas.consumer
      enabled           = true
      brokers           = var.external_kafka == null ? "${local.kafka_host}:9092" : var.external_kafka.brokers
      replicationFactor = var.external_kafka == null ? 1 : 3
      registryURL       = "http://apicurio.data.svc.cluster.local:8080/apis/ccompat/v7"
      existingSecret    = kubernetes_secret_v1.payment_events.metadata[0].name
      schemaConfigMap   = kubernetes_config_map_v1.event_schema.metadata[0].name
    }
    database = {
      tls      = var.external_database != null
      revision = var.external_database == null ? "" : nonsensitive(sha256(jsonencode(var.external_database)))
    }
    image = {
      repository = split(":", lookup(var.local_images, each.key, "missing:missing"))[0]
      tag        = split(":", lookup(var.local_images, each.key, "missing:missing"))[1]
      pullPolicy = "Never"
      digest     = ""
    }
  })]
  lifecycle {
    precondition {
      condition     = alltrue([for name in keys(var.application_replicas) : contains(keys(local.applications), name)])
      error_message = "application_replicas keys must identify implemented catalog modules with charts."
    }
    precondition {
      condition     = contains(keys(var.local_images), each.key)
      error_message = "Missing local image. Run make dev-images before planning or deploying."
    }
  }
}
