locals {
  root    = "${path.module}/../../../.."
  catalog = yamldecode(file("${local.root}/solution.yaml"))
  applications = {
    for name in local.catalog.modules : name => "${local.root}/modules/${name}/chart"
    if fileexists("${local.root}/modules/${name}/Dockerfile") && fileexists("${local.root}/modules/${name}/chart/Chart.yaml")
    && (try(yamldecode(file("${local.root}/modules/${name}/chart/values.yaml")).local.feature, "") == ""
      || contains(concat(var.streaming_enabled ? ["streaming"] : [], var.analytics_enabled ? ["analytics"] : []),
    try(yamldecode(file("${local.root}/modules/${name}/chart/values.yaml")).local.feature, "")))
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

resource "helm_release" "clickhouse_analytics" {
  count           = var.analytics_enabled ? 1 : 0
  name            = "clickhouse-analytics"
  namespace       = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  chart           = "${local.root}/modules/clickhouse-analytics/chart"
  timeout         = 600
  wait            = true
  atomic          = true
  cleanup_on_fail = true
  depends_on      = [kubernetes_secret_v1.clickhouse_analytics]

  values = [yamlencode({
    image = {
      repository = split(":", lookup(var.local_images, "clickhouse-analytics", "missing:missing"))[0]
      tag        = split(":", lookup(var.local_images, "clickhouse-analytics", "missing:missing"))[1]
      pullPolicy = "Never"
      digest     = ""
    }
  })]

  lifecycle {
    precondition {
      condition     = contains(keys(var.local_images), "clickhouse-analytics")
      error_message = "Missing local ClickHouse image. Run make dev-images before planning or deploying analytics."
    }
    precondition {
      condition     = !contains(keys(var.application_replicas), "clickhouse-analytics")
      error_message = "ClickHouse is a single-replica StatefulSet and cannot use application_replicas."
    }
  }
}

resource "helm_release" "application" {
  for_each        = { for name, chart in local.applications : name => chart if name != "clickhouse-analytics" }
  name            = each.key
  namespace       = kubernetes_namespace_v1.foundation["apps"].metadata[0].name
  chart           = each.value
  timeout         = 600
  wait            = true
  atomic          = true
  cleanup_on_fail = true
  depends_on      = [helm_release.ingress, helm_release.clickhouse_analytics, kubernetes_stateful_set_v1.postgres, kubernetes_secret_v1.payment_database, kubernetes_stateful_set_v1.kafka, kubernetes_deployment_v1.registry, kubernetes_secret_v1.payment_events, kubernetes_secret_v1.clickhouse_analytics, kubernetes_config_map_v1.event_schema, kubernetes_deployment_v1.minio]

  values = [yamlencode({
    replicaCount   = lookup(var.application_replicas, each.key, 1)
    runtimeProfile = var.runtime_profile
    streaming = {
      enabled     = var.streaming_enabled
      restorePath = var.streaming_restore_path
    }
    analytics = {
      enabled        = var.analytics_enabled
      url            = "http://clickhouse-analytics:8123"
      user           = "rpp_analytics"
      existingSecret = "clickhouse-analytics"
    }
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
      tls               = true
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
