variable "ai_gpu_enabled" {
  description = "Opt-in NVIDIA time-sliced GPU acceleration for both llama.cpp servers."
  type        = bool
  default     = false
  validation {
    condition     = !var.ai_gpu_enabled || var.ai_enabled
    error_message = "ai_gpu_enabled requires ai_enabled."
  }
}

# Minikube's default plugin is disabled by cluster startup. This release owns
# the sole device registration and advertises two shared slots per physical GPU.
resource "helm_release" "nvidia_device_plugin" {
  count     = var.ai_gpu_enabled ? 1 : 0
  name      = "rpp-nvidia-device-plugin"
  namespace = "kube-system"
  chart     = "${local.root}/.local/tools/nvidia-device-plugin-${local.tool_versions["nvidia-device-plugin"]}.tgz"
  wait      = true
  atomic    = true
  timeout   = 180
  values = [yamlencode({
    fullnameOverride        = "rpp-nvidia-device-plugin"
    componentSelectorLabels = { enabled = true }
    affinity = {
      nodeAffinity = {
        requiredDuringSchedulingIgnoredDuringExecution = {
          nodeSelectorTerms = [{
            matchExpressions = [{
              key      = "kubernetes.io/hostname"
              operator = "In"
              values   = [var.kube_context]
            }]
          }]
        }
      }
    }
    nodeSelector    = { "kubernetes.io/hostname" = var.kube_context }
    failOnInitError = true
    gfd             = { enabled = false }
    nfd             = { enabled = false }
    mps             = { enableHostPID = false }
    config = {
      default = "shared"
      map = {
        shared = yamlencode({
          version = "v1"
          flags   = { migStrategy = "none", failOnInitError = true }
          sharing = {
            timeSlicing = {
              renameByDefault            = true
              failRequestsGreaterThanOne = true
              resources                  = [{ name = "nvidia.com/gpu", replicas = 2 }]
            }
          }
        })
      }
    }
  })]
}
