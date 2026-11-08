mock_provider "kubernetes" {}
mock_provider "helm" {}
mock_provider "random" {}
mock_provider "tls" {}

variables {
  profiles              = ["rpp-1", "rpp-2", "rpp-3"]
  addresses             = ["192.0.2.1", "192.0.2.2", "192.0.2.3"]
  kube_config_path      = "/unused/kubeconfig"
  full_stack            = true
  observability_enabled = true
  cluster_sizes = {
    "1" = { kafka_brokers = 2 }
    "2" = { postgres_instances = 2 }
    "3" = { applications = { payment-api = 3 } }
  }
}

run "numbered_topology" {
  command = plan
  providers = {
    kubernetes.cluster_1 = kubernetes
    kubernetes.cluster_2 = kubernetes
    kubernetes.cluster_3 = kubernetes
    helm.cluster_1       = helm
    helm.cluster_2       = helm
    helm.cluster_3       = helm
    random               = random
    tls                  = tls
  }

  assert {
    condition = (
      var.database_primary == "pg-1" &&
      length(local.database_addresses) == 3 &&
      local.database_addresses["pg-1"] == "192.0.2.1" &&
      local.database_addresses["pg-2"] == "192.0.2.2" &&
      local.database_addresses["pg-3"] == "192.0.2.3"
    )
    error_message = "Database identities and addresses must follow cluster numbers 1/2/3."
  }
  assert {
    condition = (
      local.sizes["1"].kafka_brokers == 2 &&
      local.sizes["2"].postgres_instances == 2 &&
      local.sizes["3"].applications["payment-api"] == 3 &&
      output.workload_vars["1"].telemetry_cluster == "rpp-1" &&
      output.workload_vars["2"].telemetry_cluster == "rpp-2" &&
      output.workload_vars["3"].telemetry_cluster == "rpp-3"
    )
    error_message = "Workload counts and telemetry contexts must follow the numbered clusters."
  }
  assert {
    condition = (
      local.telemetry_settings["1"].backends &&
      !local.telemetry_settings["2"].backends &&
      !local.telemetry_settings["3"].backends &&
      output.application_vars.external_kafka.brokers == "192.0.2.1:30092,192.0.2.1:30101,192.0.2.2:30092,192.0.2.3:30092"
    )
    error_message = "The shared telemetry backends and Kafka endpoints must use the numbered topology."
  }
}

run "legacy_cluster_key_is_rejected" {
  command = plan
  providers = {
    kubernetes.cluster_1 = kubernetes
    kubernetes.cluster_2 = kubernetes
    kubernetes.cluster_3 = kubernetes
    helm.cluster_1       = helm
    helm.cluster_2       = helm
    helm.cluster_3       = helm
    random               = random
    tls                  = tls
  }
  variables { cluster_sizes = { a = {} } }
  expect_failures = [var.cluster_sizes]
}

run "legacy_database_primary_is_rejected" {
  command = plan
  providers = {
    kubernetes.cluster_1 = kubernetes
    kubernetes.cluster_2 = kubernetes
    kubernetes.cluster_3 = kubernetes
    helm.cluster_1       = helm
    helm.cluster_2       = helm
    helm.cluster_3       = helm
    random               = random
    tls                  = tls
  }
  variables { database_primary = "pg-a" }
  expect_failures = [var.database_primary]
}
