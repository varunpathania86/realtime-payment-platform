import base64
import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from scripts import resource_access as access
from scripts.module_catalog import ROOT


def services(*entries):
    return json.dumps({"items": [
        {"metadata": {"namespace": ns, "name": name}, "spec": {"ports": [{"port": port} for port in ports]}}
        for ns, name, ports in entries
    ]})


class ResourceAccessTests(unittest.TestCase):
    def test_minio_access_forwards_only_console_in_explicit_profile(self):
        for profile, selector in (("rpp", []), ("rpp-2", ["MULTI_CLUSTER=3"])):
            result = subprocess.run(
                [
                    "make", "--no-print-directory", "-n", "minio-access", *selector,
                    f"MINIKUBE_PROFILE={profile}",
                ],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            self.assertEqual(result.stdout.strip(),
                f'kubectl --context "{profile}" -n data port-forward --address 127.0.0.1 service/minio 19091:9001')

    def test_discovers_only_deployed_services_in_explicit_profile(self):
        run = Mock(return_value=services(("data", "payments-postgres", [5432])))
        endpoints = access.discover("my-profile", run)
        self.assertEqual(len(endpoints), 1)
        self.assertEqual(endpoints[0].local, 15432)
        self.assertEqual(run.call_args.args[0][:3], ["kubectl", "--context", "my-profile"])

    def test_missing_service_port_fails_instead_of_claiming_access(self):
        with self.assertRaisesRegex(RuntimeError, "does not expose"):
            access.discover("rpp", Mock(return_value=services(("apps", "lakehouse-query", [9999]))))

    def test_no_deployed_data_services_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "no supported"):
            access.discover("rpp", Mock(return_value=services()))

    def test_kafka_uses_actual_advertised_host_port(self):
        encoded = base64.b64encode(b"advertised.listeners=SASL_SSL://internal:9092,HOST://localhost:29092").decode()
        run = Mock(side_effect=[
            services(("data", "payments-kafka", [9092, 19092])),
            json.dumps({"data": {"server.properties": encoded}}),
        ])
        endpoints = access.discover("rpp", run)
        self.assertEqual(endpoints[0].local, 29092)
        self.assertEqual(endpoints[0].remote, 19092)

    def test_internal_kafka_reports_how_to_enable_desktop_access(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            endpoints = access.discover("rpp", Mock(return_value=services(
                ("data", "payments-postgres", [5432]), ("data", "payments-kafka", [9092]),
            )))
        self.assertIn("kafka_host_port=19092", output.getvalue())
        self.assertEqual(len(endpoints), 1)

    def test_kafka_advertised_port_cannot_collide_with_other_forwards(self):
        encoded = base64.b64encode(b"advertised.listeners=HOST://localhost:15432").decode()
        run = Mock(side_effect=[
            services(("data", "payments-postgres", [5432]), ("data", "payments-kafka", [9092, 19092])),
            json.dumps({"data": {"server.properties": encoded}}),
        ])
        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            access.discover("rpp", run)

    def test_local_ports_are_unique(self):
        ports = [endpoint.local for endpoint in access.ENDPOINTS] + [19092]
        self.assertEqual(len(ports), len(set(ports)))

    def test_forward_binds_loopback_and_waits_for_readiness(self):
        endpoint = access.ENDPOINTS[0]
        real_popen = subprocess.Popen
        captured = []
        def fake_popen(args, **kwargs):
            captured.extend(args)
            return real_popen(
                [sys.executable, "-u", "-c",
                    "import time; print('Forwarding from 127.0.0.1:15432 -> 5432', flush=True); time.sleep(60)"],
                **kwargs,
            )
        with patch.object(access.subprocess, "Popen", side_effect=fake_popen):
            process = access.start_forward("explicit-profile", endpoint)
        try:
            self.assertIsNone(process.poll())
            self.assertIn("explicit-profile", captured)
            self.assertEqual(captured[captured.index("--address") + 1], "127.0.0.1")
        finally:
            access.stop_forward(process)
        self.assertIsNotNone(process.poll())

    def test_forward_timeout_terminates_its_child(self):
        real_popen = subprocess.Popen
        children = []
        def fake_popen(_args, **kwargs):
            child = real_popen([sys.executable, "-u", "-c", "import time; time.sleep(60)"], **kwargs)
            children.append(child)
            return child
        with patch.object(access.subprocess, "Popen", side_effect=fake_popen):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                access.start_forward("rpp", access.ENDPOINTS[0], timeout=0.1)
        self.assertIsNotNone(children[0].poll())

    def test_partial_startup_failure_closes_started_forwards(self):
        child = Mock()
        with patch.object(access, "start_forward", side_effect=[child, RuntimeError("port occupied")]):
            with patch.object(access, "stop_forward") as stop:
                with self.assertRaisesRegex(RuntimeError, "port occupied"):
                    access.serve("rpp", access.ENDPOINTS[:2])
                stop.assert_called_once_with(child)

    def test_kafka_host_listener_keeps_internal_security_and_adds_loopback_sans(self):
        config = (ROOT / "platform/terraform/environments/local/events.tf").read_text()
        self.assertIn('default     = 0', config)
        self.assertIn('inter.broker.listener.name=SASL_SSL', config)
        self.assertIn('HOST://localhost:${var.kafka_host_port}', config)
        self.assertIn('HOST:SASL_SSL', config)
        self.assertIn('listener.name.host.scram-sha-512', config)
        self.assertIn('? ["localhost"] : []', config)
        self.assertIn('? ["127.0.0.1"] : []', config)
        self.assertIn('"checksum/config" = sha256(local.kafka_properties)', config)
        self.assertIn('name  = "KAFKA_TLS_REVISION"', config)
        self.assertIn('value = sha256(tls_self_signed_cert.kafka[0].cert_pem)', config)
