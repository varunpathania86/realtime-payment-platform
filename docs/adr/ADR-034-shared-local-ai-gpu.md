# ADR-034: Explicit shared NVIDIA GPU access for local AI

- **Status:** Accepted
- **Date:** 2026-11-09
- **Deciders:** Repository maintainer

## Context

Local llama.cpp chat and embeddings run in separate containers. The development
machine has one NVIDIA RTX A2000 with 8 GiB VRAM. Giving each container an
exclusive GPU request would require two devices; exposing all devices without
scheduler allocation would bypass Kubernetes accounting. CPU operation and the
independent local development workflow must remain available.

## Decision

Keep CPU as the default. Opt-in NVIDIA mode uses the pinned CUDA llama.cpp image,
Minikube Docker GPU passthrough and one Terraform-owned NVIDIA device-plugin
release configured for two time-sliced shared allocations per physical GPU.
Each AI server requests one `nvidia.com/gpu.shared` allocation and explicitly
requires CUDA; the model-fetch init container requests none and hides devices.
Only cluster 1 receives passthrough in a three-cluster topology.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| CPU only | No GPU prerequisites | Does not use available NVIDIA compute |
| Two exclusive GPU allocations | Device isolation | Requires two physical GPUs |
| Unallocated device visibility | Simple startup | Bypasses scheduling and resource accounting |
| Explicit time slicing | Both servers share one physical GPU with scheduler accounting | No VRAM isolation or guaranteed throughput |
| Replace llama.cpp with vLLM | Suitable for larger serving workloads | Unnecessary architecture and memory overhead for these small local models |

## Consequences

- Pins live in `.tool-versions`; `AI_GPU=1 make setup` caches and verifies the
  plugin chart and `make doctor` checks opt-in prerequisites.
- Disable Minikube's built-in plugin before applying the Terraform-owned
  release, so only one plugin registers devices.
- Enable disjoint component selectors for the vendor plugin and its
  MPS-label-gated auxiliary DaemonSet. Do not enable MPS or feature discovery;
  disable the unused helper's host-PID access.
- Explicit hostname affinity replaces the vendor's hardware-label expression
  list. An empty affinity map is insufficient because Helm retains defaults.
- GPU mode selects distinct local image tags; CPU images stay available.
- Host drivers/runtime must work first. Do not silently fall back to CPU,
  replace host drivers, restart Docker or destroy existing CPU-only profiles.
- Shared compute does not add host RAM or isolate GPU memory. Both model
  servers contend for the same VRAM and must be measured together.
- Offline tests cover rendering, pin selection, state persistence and failure
  paths. On 2026-11-10, clean `rpp` recreation and concurrent chat/embedding
  requests passed on the RTX A2000 8 GiB after WSL GPU recovery. Approximately
  1635 MiB combined VRAM was observed after initialization; sustained
  throughput and arbitrary model-memory fit are not certified.
