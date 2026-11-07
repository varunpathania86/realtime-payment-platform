import os
import subprocess
import unittest

from scripts.module_catalog import ROOT


class LocalMakeIdentityTests(unittest.TestCase):
    def test_explicit_profile_selects_single_workflow_without_changing_default(self):
        env = {key: value for key, value in os.environ.items() if key != "MINIKUBE_PROFILE"}
        for action in ("up", "update"):
            for profile in ("", "rpp-unit-single"):
                with self.subTest(action=action, profile=profile):
                    args = ["make", "--no-print-directory", "--dry-run", "dev-" + action]
                    if profile:
                        args.append("MINIKUBE_PROFILE=" + profile)
                    recipe = subprocess.run(args, cwd=ROOT, env=env, check=True,
                                            capture_output=True, text=True, timeout=10).stdout
                    # Intercept the trusted Make recipe's bash calls; no cluster operation runs.
                    command = 'bash() { printf "%s|%s\\n" "$*" "${MINIKUBE_PROFILE:-}"; }\n' + recipe
                    result = subprocess.run(["bash", "-c", command], cwd=ROOT, env=env, check=True,
                                            capture_output=True, text=True, timeout=10)
                    expected = "scripts/local-dev.sh" if profile else "scripts/multi-cluster.sh"
                    self.assertEqual(result.stdout.strip(), f"{expected} {action}|{profile}")
