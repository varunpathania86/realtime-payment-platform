#!/usr/bin/env bash
# Install Docker Engine from Docker's official apt repository (Ubuntu/Debian, incl. WSL2).
# Needs sudo (asks for your password). Idempotent: does nothing if Docker already works,
# for example Docker Desktop with WSL integration. Also makes Docker usable without sudo:
# the user joins the docker group (applies to new shells) and gets an ACL on the Docker socket
# (applies to already open shells until the daemon restarts).
set -euo pipefail

if docker info >/dev/null 2>&1; then
  echo "ok       docker $(docker --version | sed -n 's/^Docker version \([0-9.]*\).*/\1/p') (daemon reachable)"
  exit 0
fi

if ! command -v docker >/dev/null; then
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
fi

if ! sudo docker info >/dev/null 2>&1; then
  if [ -d /run/systemd/system ]; then
    sudo systemctl enable --now docker
  else
    sudo service docker start
    echo "NOTE: systemd is not enabled in this WSL distribution; start Docker with 'sudo service docker start' after each WSL restart."
  fi
fi

if ! getent group docker | grep -qw "$USER"; then
  sudo usermod -aG docker "$USER"
  echo "Added $USER to the docker group."
fi

if ! docker info >/dev/null 2>&1; then
  command -v setfacl >/dev/null || sudo apt-get install -y -qq acl
  sudo setfacl -m "u:$USER:rw" /var/run/docker.sock
fi

docker info >/dev/null 2>&1 || { echo "Docker is installed but not usable yet. See docs/developer-setup.md" >&2; exit 1; }
echo "ok       docker works without sudo"
