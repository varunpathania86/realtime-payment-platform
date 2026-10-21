# Runbook: WSL2 networking and disk problems

- **Severity:** Low
- **Owner:** Varun Pathania
- **Last reviewed:** 2026-10-07

## Symptoms

- `make cluster-up` hangs or fails pulling images
- `docker info` cannot reach the daemon
- Disk full inside WSL, or `ext4.vhdx` keeps growing
- Slow file access or builds

## Impact

Local cluster and builds are unavailable. No data loss unless the disk is full during a write.

## Diagnosis

1. Name resolution and connectivity: `curl -sI https://github.com | head -1`. A failure points at DNS or VPN.
2. Docker daemon: `docker info | head -5`. If it fails, start Docker Desktop or `sudo service docker start`.
3. Disk usage inside WSL: `df -h /` and `docker system df`.
4. Cluster state: `make cluster-status`.
5. Location of the repo: `pwd` must start with `/home/`, not `/mnt/c`.

## Mitigation

- **DNS or VPN problems:** restart WSL from Windows with `wsl --shutdown`. If DNS stays broken, set `generateResolvConf = false` in `/etc/wsl.conf` and write a working nameserver to `/etc/resolv.conf`.
- **Docker not reachable:** enable WSL integration in Docker Desktop for this distribution, or start the Docker Engine service.
- **Disk pressure:** `docker system prune -af` (removes unused images and containers), or `make cluster-down` then `make cluster-up` to recreate the cluster.
- **Shrink the WSL disk:** run `wsl --shutdown`, then in Windows PowerShell run `Optimize-VHD -Path <path to ext4.vhdx> -Mode Full` (requires Hyper-V tools).
- **Not enough memory:** raise limits in `%UserProfile%\.wslconfig` (`memory=`, `processors=`) and run `wsl --shutdown`; lower cluster size with `MINIKUBE_MEMORY=6144 make cluster-up`.

## Verification

`make doctor` passes and `make cluster-status` shows the node `Ready`.

## Prevention and follow-up

Keep the repository in the WSL filesystem, prune Docker regularly, and leave at least 20 GB free.
