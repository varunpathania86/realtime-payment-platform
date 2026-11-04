"""Reusable, verified ONNX session and deterministic advisory prediction identity."""

from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import math
import time

from .features import CONTRACT, feature_vector, reason_codes, timestamp


class Scorer:
    def __init__(self):
        import numpy as np
        import onnxruntime as ort

        self.np = np
        package = files(__package__)
        self.metadata = json.loads(package.joinpath("metadata.json").read_text())
        data = package.joinpath("model.onnx").read_bytes()
        if hashlib.sha256(data).hexdigest() != self.metadata["model_sha256"]:
            raise ValueError("model checksum does not match metadata")
        for key in ("feature_version", "model_name", "model_version", "numeric", "categorical"):
            if self.metadata[key] != CONTRACT[key]:
                raise ValueError(f"model contract mismatch: {key}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(data, sess_options=options, providers=["CPUExecutionProvider"])
        self.verify_golden()

    def score_vector(self, vector):
        inputs = {
            name: self.np.asarray(
                [[self.np.nan if vector[name] is None else vector[name]]], dtype=self.np.float32
            )
            for name in CONTRACT["numeric"]
        }
        inputs.update({
            name: self.np.asarray([[vector[name]]], dtype=str) for name in CONTRACT["categorical"]
        })
        score = float(self.session.run(None, inputs)[1][0, 1])
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("model produced an invalid probability")
        return score

    def verify_golden(self):
        golden = json.loads(files(__package__).joinpath("golden.json").read_text())
        if golden["feature_version"] != CONTRACT["feature_version"]:
            raise ValueError("golden feature version mismatch")
        if not golden["rows"] or len(golden["rows"]) != len(golden["expected_scores"]):
            raise ValueError("golden parity cases are missing or inconsistent")
        for row, expected in zip(golden["rows"], golden["expected_scores"]):
            if abs(self.score_vector(row) - expected) > CONTRACT["onnx_parity_absolute_tolerance"]:
                raise ValueError("runtime golden ONNX parity check failed")

    def predict(self, event, history, revision, original_prediction_id=None):
        vector = feature_vector(event, history)
        canonical = json.dumps(vector, sort_keys=True, separators=(",", ":"), allow_nan=False)
        feature_hash = hashlib.sha256(canonical.encode()).hexdigest()
        identity = "/".join((
            event["event_id"], CONTRACT["feature_version"], CONTRACT["model_name"],
            CONTRACT["model_version"], self.metadata["model_sha256"], feature_hash,
        ))
        prediction_id = hashlib.sha256(identity.encode()).hexdigest()
        started = time.perf_counter()
        score = self.score_vector(vector)
        elapsed_us = round((time.perf_counter() - started) * 1_000_000)
        result = {
            "schema_version": 1, "prediction_id": prediction_id,
            "original_prediction_id": original_prediction_id or prediction_id,
            "event_id": event["event_id"], "payment_id": event["payment_id"],
            "account_id": event["account_id"], "feature_timestamp": event["event_time"],
            "feature_version": CONTRACT["feature_version"], "feature_hash": feature_hash,
            "window_version": "account-currency-prior-history-v1",
            "window_revision": revision,
            "window_start_ms": timestamp(event) - 3_600_000,
            "window_end_ms": timestamp(event),
            "model_name": CONTRACT["model_name"], "model_version": CONTRACT["model_version"],
            "model_sha256": self.metadata["model_sha256"],
            "risk_score": score, "reason_codes": reason_codes(vector), "advisory_only": True,
            "prediction_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return result, vector, elapsed_us
