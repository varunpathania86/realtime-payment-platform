"""Verify keyed state/timers in an actual bounded Flink execution."""

import json

from pyflink.common import Duration, Types, WatermarkStrategy
from pyflink.common.watermark_strategy import TimestampAssigner
from pyflink.datastream import StreamExecutionEnvironment

from fixtures import dataset
from job import PaymentWindows
from processing import decode, dimension


class Timestamp(TimestampAssigner):
    def extract_timestamp(self, value, record_timestamp):
        return decode(value)["timestamp_ms"]


env = StreamExecutionEnvironment.get_execution_environment()
env.set_parallelism(1)
env.add_python_file("/opt/job/processing.py")
env.add_python_file("/opt/job/job.py")
records = [json.dumps(record["event"]) for record in dataset()]
stream = env.from_collection(records, type_info=Types.STRING()).assign_timestamps_and_watermarks(
    WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_seconds(10))
    .with_timestamp_assigner(Timestamp()))
results = stream.key_by(lambda raw: dimension(decode(raw)), key_type=Types.STRING()).process(
    PaymentWindows(), output_type=Types.STRING()).execute_and_collect()
materialized = {}
with results:
    for raw in results:
        value = json.loads(raw)
        materialized[value["result_id"]] = value
minutes = [value for value in materialized.values() if value["window_end"] - value["window_start"] == 60_000]
assert sum(value["payments"] for value in minutes) == len({record["event_id"] for record in map(json.loads, records)})
assert any(value["rejections"] for value in minutes)
assert all(value["revision"] >= 1 for value in materialized.values())
print(json.dumps({"bounded_flink_execution": "passed", "logical_windows": len(materialized)}))
