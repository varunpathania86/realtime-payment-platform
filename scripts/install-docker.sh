#!/usr/bin/env bash
# Install Docker Engine from Docker's official apt repository (Ubuntu/Debian, incl. WSL2).
# Needs sudo (asks for your password). Idempotent: does nothing if Docker already works,
# for example Docker Desktop with WSL integration.
set -euo pipefail

if docker info >/dev/null 2>&1; then
  echo "ok       docker $(docker --version | sed -n 's/^Docker version \([0-9.]*\).*/\1/p') (daemon reachable)"
  exit 0
fi

if ! command -v apt-get >/dev/null; then
  echo "Automatic Docker install supports apt-based systems only. See docs/developer-setup.md" >&2
  exit 1
fi

# shellcheck source=/dev/null
. /etc/os-release
distro="$ID"
case "$distro" in
  ubuntu | debian) ;;
  *) echo "Unsupported distribution '$distro'. See docs/developer-setup.md" >&2; exit 1 ;;
esac
codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"

if ! curl -fsSI "https://download.docker.com/linux/$distro/dists/$codename/Release" >/dev/null; then
  echo "Docker does not publish packages for $distro $codename yet. See docs/developer-setup.md" >&2
  exit 1
fi

echo "Installing Docker Engine (sudo required)"
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL "https://download.docker.com/linux/$distro/gpg" -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro $codename stable" |
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update -qq
sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if [ -d /run/systemd/system ]; then
  sudo systemctl enable --now docker
else
  sudo service docker start
  echo "NOTE: systemd is not enabled in this WSL distribution; start Docker with 'sudo service docker start' after each WSL restart."
fi

sudo usermod -aG docker "$USER"

if sudo docker info >/dev/null 2>&1; then
  echo "ok       docker installed and running"
else
  echo "Docker was installed but the daemon is not running. See docs/developer-setup.md" >&2
  exit 1
fi

echo
echo "IMPORTANT: your user was added to the 'docker' group. Open a new shell for it to take effect"
echo "           (in WSL: run 'wsl --shutdown' from Windows PowerShell, then reopen the terminal)."
