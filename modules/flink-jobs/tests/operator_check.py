"""Execute the real PyFlink operator against deterministic state/timer contexts."""

import json
import io
import os
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from fixtures import event
from job import PaymentWindows, TOO_LATE, kafka_properties
from pyflink.java_gateway import get_gateway
from processing import decode, dimension
import runtime
import control


class State:
    def __init__(self):
        self.data = {}

    def contains(self, key):
        return key in self.data

    def put(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def items(self):
        return self.data.items()

    def remove(self, key):
        self.data.pop(key)


class Counter:
    def __init__(self):
        self.value = 0

    def inc(self, value=1):
        self.value += value


class Metrics:
    def __init__(self):
        self.counters = {}

    def counter(self, name):
        self.counters[name] = Counter()
        return self.counters[name]


class Runtime:
    def __init__(self):
        self.states = {}
        self.metrics = Metrics()

    def get_map_state(self, descriptor):
        name = descriptor.get_name()
        return self.states.setdefault(name, State())

    def get_metrics_group(self):
        return self.metrics


class Context:
    def __init__(self):
        self.watermark = -1
        self.timers = set()
        self.event_timestamp = None

    def timestamp(self):
        return self.event_timestamp

    def timer_service(self):
        return self

    def current_watermark(self):
        return self.watermark

    def current_processing_time(self):
        return decode(json.dumps(event(0, 1000)))["timestamp_ms"]

    def register_event_time_timer(self, timestamp):
        self.timers.add(timestamp)

    def get_current_key(self):
        return dimension(event(0))

    def advance(self, timestamp, operator):
        self.watermark = timestamp
        output = []
        for timer in sorted(self.timers):
            if timer <= timestamp:
                output.extend(operator.on_timer(timer, self))
                self.timers.remove(timer)
        return output


class OperatorTests(unittest.TestCase):
    def test_submit_distributes_all_imported_job_modules(self):
        with patch.object(runtime, "overview", return_value=[]), \
                patch.object(runtime, "bootstrap"), \
                patch.object(runtime, "wait_for_checkpoint"), \
                patch.object(runtime.subprocess, "run") as run:
            runtime.submit()
        command = run.call_args.args[0]
        self.assertEqual(
            command[command.index("--pyFiles") + 1].split(","),
            ["/opt/job/processing.py", "/opt/job/analytics.py"],
        )

    def test_inspection_initializes_explicit_positions_before_checking_progress(self):
        consumer = unittest.mock.MagicMock()
        consumer.partitions_for_topic.return_value = {0}
        results = control.TopicPartition("payment-window-results-v1", 0)
        late = control.TopicPartition("payment-events-too-late", 0)
        consumer.beginning_offsets.return_value = {results: 0, late: 0}
        consumer.end_offsets.return_value = {results: 0, late: 0}
        consumer.position.return_value = 0
        with patch.object(control, "connection", return_value={}), \
                patch.object(control, "KafkaConsumer", return_value=consumer), redirect_stdout(io.StringIO()):
            control.inspect()
        self.assertEqual(consumer.seek.call_count, 2)
        consumer.seek_to_beginning.assert_not_called()
        consumer.close.assert_called_once()

    def test_fixture_and_recovery_producers_use_contract_timestamps_and_bounded_duplicates(self):
        for action, expected in ((lambda: control.produce("offline-test"), 32), (control.load, 512)):
            producer = unittest.mock.MagicMock()
            with patch.object(control, "connection", return_value={}), \
                    patch.object(control, "KafkaProducer", return_value=producer), \
                    patch.object(control.time, "sleep"), redirect_stdout(io.StringIO()):
                action()
            self.assertEqual(producer.send.call_count, expected)
            for call in producer.send.call_args_list:
                options = call.kwargs
                self.assertEqual(options["timestamp_ms"], decode(options["value"])["timestamp_ms"])
                self.assertIn(options["partition"], (0, 1))
            producer.close.assert_called_once_with(timeout=10)

    def test_submit_readiness_requires_a_completed_checkpoint(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        job = {"name": runtime.JOB_NAME, "jid": "synthetic-job", "state": "RUNNING"}
        with patch.object(runtime, "overview", return_value=[job]), \
                patch.object(runtime.urllib.request, "urlopen", return_value=response), \
                patch.object(runtime.json, "load", side_effect=[
                    {"latest": {"completed": None}}, {"latest": {"completed": {"id": 1}}},
                ]), patch.object(runtime.time, "sleep") as sleep:
            runtime.wait_for_checkpoint()
            sleep.assert_called_once_with(2)
        with patch.object(runtime, "overview", return_value=[{**job, "state": "FAILED"}]):
            with self.assertRaisesRegex(RuntimeError, "FAILED"):
                runtime.wait_for_checkpoint()

    def test_scram_login_module_exists_in_the_pinned_connector(self):
        with patch.dict(os.environ, {"KAFKA_BROKERS": "localhost:9092", "KAFKA_TLS": "true",
                "KAFKA_PASSWORD": "synthetic-test-only"}):
            module = kafka_properties()["sasl.jaas.config"].split()[0]
        loader = get_gateway().jvm.java.lang.Thread.currentThread().getContextClassLoader()
        self.assertEqual(loader.loadClass(module).getName(), module)

    def process(self, operator, context, value):
        raw = json.dumps(value)
        context.event_timestamp = decode(raw)["timestamp_ms"]
        return list(operator.process_element(raw, context))

    def test_late_revision_dedup_timers_cleanup_and_side_output(self):
        operator = PaymentWindows()
        runtime = Runtime()
        operator.open(runtime)
        ctx = Context()
        base = decode(json.dumps(event(0)))["timestamp_ms"]
        self.assertEqual(self.process(operator, ctx, event(1, 10)), [])
        self.process(operator, ctx, event(1, 10))
        emitted = [json.loads(value) for value in ctx.advance(base + 70_000, operator)]
        minute = next(value for value in emitted if value["window_end"] - value["window_start"] == 60_000)
        self.assertEqual(minute["payments"], 1)
        late = [json.loads(value) for value in self.process(operator, ctx, event(2, 20))]
        revised = next(value for value in late if value["result_id"] == minute["result_id"])
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["payments"], 2)
        ctx.advance(base + 900_000, operator)
        self.assertEqual(list(operator.windows.items()), [])
        side = self.process(operator, ctx, event(3, 5))
        self.assertEqual(len(side), 1)
        self.assertEqual(side[0][0], TOO_LATE)
        self.assertEqual(json.loads(side[0][1])["reason"], "all-windows-expired")
        self.assertEqual(runtime.metrics.counters["duplicate_event_count"].value, 1)
        self.assertEqual(runtime.metrics.counters["late_event_count"].value, 1)
        self.assertEqual(runtime.metrics.counters["too_late_event_count"].value, 1)

    def test_lateness_cleanup_boundary_is_exclusive(self):
        operator = PaymentWindows()
        operator.open(Runtime())
        ctx = Context()
        base = decode(json.dumps(event(0)))["timestamp_ms"]
        ctx.watermark = base + 180_000
        result = self.process(operator, ctx, event(1, 10))
        self.assertFalse(any(isinstance(value, tuple) for value in result))
        # The one-minute window is expired; overlapping ten-minute windows remain open.
        self.assertFalse(any(json.loads(raw)["window_end"] - json.loads(raw)["window_start"] == 60_000
            for _, raw in operator.windows.items()))


if __name__ == "__main__":
    unittest.main()
