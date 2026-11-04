"""Export real generated operator hashes for savepoint compatibility checks."""

import argparse
import json

import job
from pyflink.java_gateway import get_gateway

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--legacy", action="store_true")
if parser.parse_args().legacy:
    job.preserve_result_sink_identity = lambda _env: None
env = job.build()
stream_graph = env._j_stream_execution_environment.getStreamGraph()
hashes = get_gateway().jvm.org.apache.flink.streaming.api.graph.StreamGraphHasherV2() \
    .traverseStreamGraphAndGenerateHashes(stream_graph)
identities = {}
for node in stream_graph.getStreamNodes().toArray():
    if node.getTransformationUID() is not None or (
        node.getOperatorName().startswith("results[") and
        node.getOperatorName().endswith((": Writer", ": Committer"))
    ):
        identifier = node.getUserHash() or bytes(value & 255 for value in hashes.get(node.getId())).hex()
        identities[identifier] = {"name": node.getOperatorName(), "uid": node.getTransformationUID()}
print(json.dumps(identities, sort_keys=True))
