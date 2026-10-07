# Runbook: Self-hosted runner (agent) for the Deploy workflow

- **Severity:** Low
- **Owner:** Varun Pathania
- **Last reviewed:** 2026-10-07

## Symptoms

- A Deploy job stays queued: "Waiting for a runner to pick up this job"
- `make agent-status` shows the runner offline

## Impact

Deployments to the matching environment cannot run. Build and Publish are not affected; they use GitHub-hosted runners.

## Dedicated WSL instance (recommended on a shared or company laptop)

Run the runner in its own WSL instance. It keeps the runner's rights, Docker and files apart from your learning instance, and you can delete it with one command. Never use an employer-managed instance.

Step 1: Create the instance from Windows (PowerShell or cmd). A reboot may be needed the first time, because the VirtualMachinePlatform feature is enabled:

```powershell
wsl --install Debian --name rpp-runner
wsl -l -v
```

Debian and Ubuntu are the distros supported by `make setup` and `scripts/install-docker.sh`.

Step 2: Open it with `wsl -d rpp-runner`, then `cd ~` (not `/mnt/c/...`).

Step 3: Check systemd, which the runner service needs: `cat /etc/wsl.conf` must contain `[boot]` and `systemd=true`, and `systemctl is-system-running` must print `running`. If not, add the section, run `wsl --terminate rpp-runner` from Windows, and reopen.

Step 4: Install the prerequisites. A fresh Debian image has no Python, no venv module and no `make`:

```bash
sudo apt-get update
sudo apt-get install -y git make curl ca-certificates python3 python3-venv
python3 --version
python3 -m venv --help > /dev/null && echo "venv OK"
```

Python is needed by `make setup` and by the tool installer. `python3-venv` lets `make setup` create a virtual environment in `.venv/` inside the repository, where it installs `pre-commit` (the lint hooks). Nothing is installed system-wide with pip, and `.venv/` is git-ignored. The full prerequisite list is in [developer-setup.md](../developer-setup.md).

On a WSL instance, apt may end with `Job for systemd-binfmt.service failed`. This is harmless: WSL already registers its own binfmt handler, so the service cannot register it again. Confirm with `sudo dpkg --audit` (no output means the install finished) and continue.

If `python3 -m venv` fails with "ensurepip is not available", `python3-venv` is missing: install it and delete a half-created `.venv/` with `rm -rf .venv`, then repeat `make setup`.

Step 5: Nothing to authenticate for the clone while the repository is public. If it is private, add a read-only deploy key (Settings, Deploy keys) or run `gh auth login`; the instance's home is separate from your other instances.

Step 6: Clone the repository and continue with [Setup](#setup):

```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/varunpathania86/realtime-payment-platform.git
cd realtime-payment-platform
make setup
make setup-agent
```

`make setup` ends with `make doctor`. If it reports `~/.local/bin is not on PATH`, run `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc`, then `make doctor`. A `warn` line for Node.js is fine: Node is optional and only needed to build the React app (see [developer-setup.md](../developer-setup.md)).

`make setup` installs the pinned tools and Docker, and asks for your sudo password once. Until the Stage 0 pull request is merged, run `git checkout feature/stage-0-workstation-baseline` before `make setup`, because `main` does not have these targets yet. If `make setup` stops with "python3 is required", repeat Step 4 (Python and venv).

To remove everything: `make remove-agent`, then `wsl --unregister rpp-runner` from Windows. This deletes only that instance.

## Setup

```bash
make setup-agent
```

The command downloads the pinned runner (version in `.tool-versions`, SHA-256 verified) into `~/actions-runner/`, installs its OS dependencies and the systemd service (sudo is required), and registers it for this repository with the label `local`. A registration token comes from the `gh` CLI if it is logged in (`gh auth login`); otherwise the command asks you to paste one from the repository's Settings, Actions, Runners page.

Options (environment variables): `AGENT_LABELS` (default `local`; use `vps` on the VPS), `AGENT_NAME` (default `rpp-<first label>`, for example `rpp-local`; the hostname is deliberately not used because it is public in workflow logs), `AGENT_DIR`, `AGENT_SERVICE=0` to skip the service and run `./run.sh` manually.

No static IP or open ports are needed: the runner only makes outbound HTTPS connections to GitHub.

## Diagnosis

1. `make agent-status` shows the service state and the runner status on GitHub.
2. Is the laptop awake and WSL running? WSL stops when idle; `wsl --shutdown` stops the runner.
3. Service logs: `journalctl -u 'actions.runner.*' -n 50`.
4. Outbound access to `github.com` and `*.actions.githubusercontent.com` must not be blocked by a VPN or firewall.

## Mitigation

- Start the service: `cd ~/actions-runner/<repo> && sudo ./svc.sh start`, or run `./run.sh` in a terminal.
- Re-register: `make remove-agent && make setup-agent`.

## Security

- The repository is public, so anyone can open a PR from a fork. In Settings, Actions, General: require approval for fork pull request workflows and set default workflow permissions to read-only.
- Create the `local` environment with a required reviewer, so every deploy job waits for your approval.
- Use the runner only for Deploy. Never run pull requests from forks on it.
- Keep the Deploy workflow manual (`workflow_dispatch`) and the `local` environment protected with a required reviewer.
- The runner executes jobs with your user's rights. Prefer a dedicated low-privilege user.

## Removal

`make remove-agent` stops the service, unregisters the runner, and deletes its directory.
