"""Verify deployed ONNX package, real Flink inference and late-history revisions."""

import json
import os
from pyflink.common import Duration, Types, WatermarkStrategy
from pyflink.common.watermark_strategy import TimestampAssigner
from pyflink.datastream import StreamExecutionEnvironment

from fixtures import event
from payment_risk.scorer import Scorer
from processing import LATENESS_MS, decode
from risk import RiskPayments, account_currency
from operator_check import Context, Runtime


class RiskMetrics:
    def __init__(self):
        self.values = {}

    def add_group(self, *_args):
        return self

    def counter(self, name):
        from operator_check import Counter

        counter = Counter()
        self.values[name] = counter
        return counter


class RiskRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.metrics = RiskMetrics()


scorer = Scorer()
operator = RiskPayments()
runtime = RiskRuntime()
operator.open(runtime)
ctx = Context()
first, later, late = event(700, 0), event(702, 100), event(701, 50, outcome="rejected")


def process(record):
    ctx.event_timestamp = decode(json.dumps(record))["timestamp_ms"]
    return [json.loads(value) for value in operator.process_element(json.dumps(record), ctx)]


process(first)
original = process(later)[0]
ctx.watermark = decode(json.dumps(late))["timestamp_ms"] + 1000
revisions = process(late)
revised = next(item for item in revisions if item["event_id"] == later["event_id"])
assert revised["window_revision"] == 2
assert revised["original_prediction_id"] == original["prediction_id"]
assert revised["prediction_id"] != original["prediction_id"]
assert process(late) == []
assert all(item["advisory_only"] for item in revisions)
ctx.watermark = decode(json.dumps(later))["timestamp_ms"] + LATENESS_MS
expired = event(703, 0)
ctx.event_timestamp = decode(json.dumps(expired))["timestamp_ms"]
side = list(operator.process_element(json.dumps(expired), ctx))
assert len(side) == 1 and side[0][0].tag_id == "risk-too-late"
ctx.advance(decode(json.dumps(later))["timestamp_ms"] + 3_600_000 + LATENESS_MS, operator)
assert not runtime.states["risk-history-v1"].data
assert not runtime.states["risk-predictions-v1"].data

env = StreamExecutionEnvironment.get_execution_environment()
env.set_parallelism(1)
for path in ("/opt/job/processing.py", "/opt/job/risk.py", os.environ["RISK_TEST_PACKAGE"]):
    env.add_python_file(path)


def verify_in_worker(_value):
    from payment_risk.scorer import Scorer

    session = Scorer()
    session.verify_golden()
    return json.dumps({"golden_parity": True, "model_version": session.metadata["model_version"]})


results = env.from_collection(["golden"], type_info=Types.STRING()).map(
    verify_in_worker, output_type=Types.STRING()).execute_and_collect()
with results:
    records = list(results)
assert len(records) == 1 and json.loads(records[0])["golden_parity"]
print(json.dumps({"risk_operator_revisions": "passed", "flink_worker_golden_parity": "passed",
    "model_version": scorer.metadata["model_version"]}))


class EventTimestamp(TimestampAssigner):
    def extract_timestamp(self, value, record_timestamp):
        from processing import decode

        return decode(value)["timestamp_ms"]


stream = env.from_collection(
    [json.dumps(item) for item in (first, later, late)], type_info=Types.STRING(),
).assign_timestamps_and_watermarks(
    WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_seconds(10))
        .with_timestamp_assigner(EventTimestamp()))
predictions = stream.key_by(account_currency, key_type=Types.STRING()).process(
    RiskPayments(), output_type=Types.STRING()).execute_and_collect()
with predictions:
    audit = [json.loads(value) for value in predictions]
payment_audit = [value for value in audit if value["event_id"] == later["event_id"]]
assert len(payment_audit) == 2
assert {value["window_revision"] for value in payment_audit} == {1, 2}
print(json.dumps({"bounded_risk_execution": "passed", "audit_records": len(audit)}))
