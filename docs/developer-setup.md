# Developer setup

How to prepare a machine to work on this repository. Target environment: Windows with WSL2 (Ubuntu), VS Code Remote - WSL. Native Linux and macOS work the same way.

Work inside the WSL filesystem (for example `~/code`), not under `/mnt/c`, for speed and correct line endings.

## 1. Prerequisites

| Tool | Needed for | Install (Ubuntu/WSL) |
|---|---|---|
| git | Version control | `sudo apt install git` |
| make | All workflows | `sudo apt install make` |
| python3 with venv | pre-commit | `sudo apt install python3 python3-venv` |
| SSH key on GitHub | Push access | [GitHub docs](https://docs.github.com/en/authentication/connecting-to-github-with-ssh); verify with `ssh -T git@github.com` |
| GitHub CLI (optional) | Creating pull requests | [cli.github.com](https://cli.github.com); then `gh auth login` |

`make setup` installs everything else in the next section. Docker and Node.js are only checked, see below.

### Pinned tools

Versions are pinned in [.tool-versions](../.tool-versions). `make setup` (or `make tools`) downloads kubectl, Minikube, Helm, Terraform, Go, jq, and yq into `~/.local/bin` without sudo, verifying each download with SHA-256. Go is unpacked to `~/.local/go`. Tools already at the pinned version are skipped, so the command is safe to repeat.

Make sure `~/.local/bin` is on your PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

To upgrade a tool, change its version in `.tool-versions` and run `make tools`.

### Docker

Minikube runs on the Docker driver, so Docker must be installed and its daemon reachable. `make setup` does not install it because the options need admin rights or Windows-side action. Choose one:

- **Docker Desktop (Windows):** install it, then enable Settings → Resources → WSL integration for your distribution.
- **Docker Engine in WSL2:** follow the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/), then `sudo usermod -aG docker $USER` and restart the WSL shell (`wsl --shutdown` from Windows).

Verify with `docker info`.

### Node.js

Node.js 22 is required for the React portal (pnpm or npm). Install it with your preferred manager, for example [nvm](https://github.com/nvm-sh/nvm): `nvm install 22`.

## 2. One-time setup

```bash
git clone git@github.com:varunpathania86/realtime-payment-platform.git
cd realtime-payment-platform
make setup
```

`make setup`:

1. Installs `pre-commit` into the git-ignored `.venv/` (skipped if `pre-commit` is already on your PATH).
2. Installs the git pre-commit hook, so checks run on every commit.
3. Installs the pinned tools from `.tool-versions` (`scripts/install-tools.sh`).
4. Runs `make doctor`, which compares everything with `.tool-versions` and lists problems (for example a missing Docker).

## 3. Daily commands

| Command | Purpose |
|---|---|
| `make help` | List all targets |
| `make doctor` | Check the environment against `.tool-versions` |
| `make tools` | Install or update the pinned tools |
| `make cluster-up` | Start the local Minikube cluster (profile `rpp`) |
| `make cluster-status` | Show cluster status |
| `make cluster-down` | Delete the cluster and its data (destructive) |
| `make ci` | Lint, test, and build; same as the Build workflow |
| `make lint` | Shared static checks and per-project lint |
| `make fmt` | Format all sub-projects |
| `make projects` | List discovered sub-projects |

## 4. Workflow summary

```mermaid
flowchart LR
  A[git switch -c feature/name] --> B[edit]
  B --> C[git commit: pre-commit hooks run]
  C --> D[make ci]
  D --> E[git push, open PR]
  E --> F[Build workflow, squash merge]
```

Branch, commit, and versioning rules are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## 5. Troubleshooting

For WSL networking and disk problems see the [WSL runbook](runbooks/wsl-troubleshooting.md).

| Problem | Fix |
|---|---|
| `make doctor` reports `PROBLEM ...` | Follow the hint printed on that line; most are fixed by `make setup` |
| `~/.local/bin is not on PATH` | Add it to `~/.bashrc` as shown above |
| `docker daemon is not reachable` | Start Docker Desktop and enable WSL integration, or start the Docker service |
| `python3 -m venv` fails | `sudo apt install python3-venv` |
| `Permission denied (publickey)` on push | Check the key with `ssh -T git@github.com` and that the remote uses the SSH URL (`git remote -v`) |
| Hooks fail on line endings | Work in the WSL filesystem; the repo enforces LF via `.gitattributes` |
| Hook environments are broken or stale | `.venv/bin/pre-commit clean` (or `pre-commit clean`), then `make lint` |
