"""Compile the real Kafka, side-output and keyed-table sink graph offline."""

import json

from job import build

plan = json.loads(build().get_execution_plan())
assert len(plan["nodes"]) >= 4
print(json.dumps({"kafka_table_graph": "passed", "nodes": len(plan["nodes"])}))
