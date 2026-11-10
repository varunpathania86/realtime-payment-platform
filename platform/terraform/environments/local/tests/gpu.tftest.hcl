mock_provider "kubernetes" {}
mock_provider "helm" {}
mock_provider "random" {}
mock_provider "tls" {}

variables {
  postgres_password = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  kube_config_path  = "/unused/kubeconfig"
}

run "cpu_default_has_no_gpu_plugin" {
  command = plan
  plan_options {
    target = [helm_release.nvidia_device_plugin]
  }
  assert {
    condition     = length(helm_release.nvidia_device_plugin) == 0
    error_message = "Default CPU operation must not provision GPU infrastructure."
  }
}

run "both_servers_share_two_explicit_slots" {
  command = plan
  plan_options {
    target = [helm_release.nvidia_device_plugin]
  }
  variables {
    ai_enabled        = true
    analytics_enabled = true
    ai_gpu_enabled    = true
    kube_context      = "rpp-1"
  }
  assert {
    condition = (
      length(helm_release.nvidia_device_plugin) == 1 &&
      helm_release.nvidia_device_plugin[0].namespace == "kube-system" &&
      endswith(helm_release.nvidia_device_plugin[0].chart, "nvidia-device-plugin-${local.tool_versions["nvidia-device-plugin"]}.tgz") &&
      yamldecode(helm_release.nvidia_device_plugin[0].values[0]).componentSelectorLabels.enabled &&
      !yamldecode(helm_release.nvidia_device_plugin[0].values[0]).mps.enableHostPID &&
      yamldecode(helm_release.nvidia_device_plugin[0].values[0]).affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].values == ["rpp-1"] &&
      yamldecode(helm_release.nvidia_device_plugin[0].values[0]).nodeSelector["kubernetes.io/hostname"] == "rpp-1"
    )
    error_message = "Only the selected GPU node may run the single pinned device-plugin release."
  }
  assert {
    condition = (
      yamldecode(yamldecode(helm_release.nvidia_device_plugin[0].values[0]).config.map.shared).sharing.timeSlicing.renameByDefault &&
      yamldecode(yamldecode(helm_release.nvidia_device_plugin[0].values[0]).config.map.shared).sharing.timeSlicing.failRequestsGreaterThanOne &&
      yamldecode(yamldecode(helm_release.nvidia_device_plugin[0].values[0]).config.map.shared).sharing.timeSlicing.resources == [{ name = "nvidia.com/gpu", replicas = 2 }]
    )
    error_message = "Explicit time slicing must advertise two renamed shared slots, not two exclusive GPUs."
  }
}

run "gpu_without_ai_is_rejected" {
  command = plan
  plan_options {
    target = [helm_release.nvidia_device_plugin]
  }
  variables {
    ai_enabled     = false
    ai_gpu_enabled = true
  }
  expect_failures = [var.ai_gpu_enabled]
}

run "knowledge_database_probes_tolerate_shared_host_startup" {
  command = plan
  plan_options {
    target = [kubernetes_stateful_set_v1.knowledge_postgres]
  }
  variables {
    ai_enabled        = true
    analytics_enabled = true
  }
  assert {
    condition = (
      kubernetes_stateful_set_v1.knowledge_postgres[0].spec[0].template[0].spec[0].container[0].readiness_probe[0].timeout_seconds == 5 &&
      kubernetes_stateful_set_v1.knowledge_postgres[0].spec[0].template[0].spec[0].container[0].liveness_probe[0].timeout_seconds == 5 &&
      kubernetes_stateful_set_v1.knowledge_postgres[0].spec[0].template[0].spec[0].container[0].liveness_probe[0].failure_threshold == 6 &&
      kubernetes_stateful_set_v1.knowledge_postgres[0].spec[0].template[0].spec[0].container[0].startup_probe[0].failure_threshold == 30
    )
    error_message = "Knowledge PostgreSQL needs bounded startup grace and health-check execution time on a busy local host."
  }
}
