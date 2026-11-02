import contextlib
from copy import deepcopy
from io import StringIO
import subprocess
import unittest
from unittest.mock import patch

from scripts.resource_status import command_json, main, quantity, resource, snapshot, workload_rows


POD = {
    "metadata": {"namespace": "kafka", "name": "payments-kafka-0"},
    "spec": {"containers": [{
        "name": "kafka", "resources": {
            "requests": {"cpu": "100m", "memory": "384Mi"},
            "limits": {"cpu": "1", "memory": "512Mi"},
        },
    }]},
    "status": {"phase": "Running", "containerStatuses": [{
        "name": "kafka", "ready": True, "restartCount": 2, "state": {"running": {}},
    }]},
}
METRICS = {"items": [{
    "metadata": POD["metadata"],
    "containers": [{"name": "kafka", "usage": {"cpu": "123456789n", "memory": "350000Ki"}}],
}]}
NODE = {
    "HostConfig": {"NanoCpus": 2000000000, "Memory": 3221225472},
    "State": {"Status": "running", "Running": True},
}
STATS = {"CPUPerc": "45.00%", "MemUsage": "1.5GiB / 3GiB", "MemPerc": "50.00%"}


class ResourceStatusTests(unittest.TestCase):
    def test_quantity_units_and_exact_resource_format(self):
        for value in ("1", "1000m", "1000000u", "1000000000n", "1e0"):
            self.assertEqual(resource(value, "cpu"), "1000.0")
        self.assertEqual(resource("1Gi", "memory"), "1024.0")
        self.assertEqual(resource("1048576", "memory"), "1.0")
        self.assertEqual(quantity("1M"), 1000000)
        self.assertEqual(resource(None, "cpu"), "-")
        for value in ("bad", "1foo", "", "NaN"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quantity(value)

    def test_actual_usage_requests_limits_and_readiness(self):
        rows, missing = workload_rows("lab-a", {"items": [POD]}, METRICS)
        self.assertEqual(missing, [])
        self.assertEqual(rows, [[
            "lab-a", "kafka", "payments-kafka-0", "kafka", "Running", "yes", "2",
            "123.5", "341.8", "100.0/1000.0", "384.0/512.0",
        ]])

    def test_multicontainer_missing_sample_unset_limits_and_system_exclusion(self):
        pod = deepcopy(POD)
        pod["spec"]["containers"].append({"name": "sidecar"})
        pod["status"]["containerStatuses"].append({
            "name": "sidecar", "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        })
        system = deepcopy(POD)
        system["metadata"]["namespace"] = "kube-system"
        rows, missing = workload_rows("lab-a", {"items": [pod, system]}, {})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][4], "CrashLoopBackOff")
        self.assertEqual(rows[1][-4:], ["N/A", "N/A", "-/-", "-/-"])
        self.assertEqual(missing, ["kafka/payments-kafka-0/kafka"])

    def test_explicit_contexts_and_bounded_commands(self):
        with patch("scripts.resource_status.command_json", side_effect=[
            [NODE], STATS, {"items": [POD]}, METRICS,
        ]) as run:
            row, rows, errors = snapshot("lab-a")
        self.assertEqual(errors, [])
        self.assertEqual(row, ["lab-a", "running", "45.00%", "2", "1.5GiB", "3072", "50.00%"])
        self.assertEqual(len(rows), 1)
        for call in run.call_args_list[2:]:
            self.assertEqual(call.args[0][:4], ["kubectl", "--context", "lab-a", "--request-timeout=10s"])
        with patch("scripts.resource_status.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "{}")) as run:
            self.assertEqual(command_json(["docker", "inspect"]), {})
            self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_stopped_node_does_not_contact_kubernetes_or_invent_usage(self):
        node = {**NODE, "State": {"Status": "exited", "Running": False}}
        with patch("scripts.resource_status.command_json", return_value=[node]) as run:
            row, rows, errors = snapshot("lab-b")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(row[1:4], ["exited", "N/A", "2"])
        self.assertEqual(rows, [])
        self.assertIn("exited", errors[0])

    def test_missing_metrics_preserves_requests_and_emits_actionable_error(self):
        with patch("scripts.resource_status.command_json", side_effect=[
            [NODE], STATS, {"items": [POD]}, RuntimeError("metrics API unavailable"),
        ]):
            _, rows, errors = snapshot("lab-c")
        self.assertEqual(rows[0][7:9], ["N/A", "N/A"])
        self.assertEqual(rows[0][-2:], ["100.0/1000.0", "384.0/512.0"])
        self.assertIn("make dev-metrics", errors[0])

    def test_partial_failures_keep_all_tables_and_return_nonzero(self):
        good = (["lab-a", "running", "10%", "2", "1GiB", "3072", "33%"], [], [])
        bad = (["lab-b", "ERROR", "N/A", "N/A", "N/A", "N/A", "N/A"], [], ["lab-b: unreachable"])
        output, errors = StringIO(), StringIO()
        with (
            patch("scripts.resource_status.snapshot", side_effect=[good, bad]),
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors),
        ):
            self.assertEqual(main(["lab-a", "lab-b"]), 1)
        self.assertIn("CPU LIMIT", output.getvalue())
        self.assertIn("CPU REQ/LIM", output.getvalue())
        self.assertIn("lab-b", output.getvalue())
        self.assertIn("WARNING: lab-b: unreachable", errors.getvalue())

    def test_command_failures_do_not_become_empty_success(self):
        for error in (
            subprocess.TimeoutExpired(["kubectl"], 20),
            subprocess.CalledProcessError(1, ["kubectl"], stderr="connection refused"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch("scripts.resource_status.subprocess.run", side_effect=error),
                self.assertRaises(RuntimeError),
            ):
                command_json(["kubectl"])
