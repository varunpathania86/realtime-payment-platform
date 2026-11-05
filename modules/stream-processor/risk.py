"""Checkpointed advisory predictions with late-history audit revisions."""

import json

from pyflink.common import Time, Types
from pyflink.datastream.functions import KeyedProcessFunction
from pyflink.datastream.output_tag import OutputTag
from pyflink.datastream.state import MapStateDescriptor, StateTtlConfig

from processing import DEDUP_TTL_MS, LATENESS_MS, decode

RISK_TOO_LATE = OutputTag("risk-too-late", Types.STRING())


def account_currency(raw):
    event = decode(raw)
    return json.dumps([event["account_id"], event["currency"]], separators=(",", ":"))


class RiskPayments(KeyedProcessFunction):
    def open(self, runtime_context):
        from payment_risk.scorer import Scorer

        self.scorer = Scorer()
        ttl = StateTtlConfig.new_builder(Time.milliseconds(DEDUP_TTL_MS)) \
            .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite) \
            .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired).build()
        seen = MapStateDescriptor("risk-seen-v1", Types.STRING(), Types.BOOLEAN())
        seen.enable_time_to_live(ttl)
        self.seen = runtime_context.get_map_state(seen)
        self.history = runtime_context.get_map_state(
            MapStateDescriptor("risk-history-v1", Types.STRING(), Types.STRING()))
        self.predictions = runtime_context.get_map_state(
            MapStateDescriptor("risk-predictions-v1", Types.STRING(), Types.STRING()))
        metrics = runtime_context.get_metrics_group().add_group(
            "risk_model", self.scorer.metadata["model_version"])
        self.count = metrics.counter("prediction_count")
        self.revisions = metrics.counter("prediction_revision_count")
        self.inference_us = metrics.counter("inference_duration_total_us")
        self.too_late = metrics.counter("risk_too_late_event_count")

    def process_element(self, raw, ctx):
        event = decode(raw)
        stamp = event["timestamp_ms"]
        if ctx.timestamp() != stamp:
            raise ValueError("risk input CreateTime must equal event_time")
        if self.seen.contains(event["event_id"]):
            return
        watermark = ctx.timer_service().current_watermark()
        self.seen.put(event["event_id"], True)
        if watermark >= stamp + LATENESS_MS:
            self.too_late.inc()
            yield RISK_TOO_LATE, json.dumps({
                "event": event, "reason": "risk-feature-lateness-expired",
                "watermark_ms": watermark,
            }, sort_keys=True)
            return
        self.history.put(event["event_id"], json.dumps(event, sort_keys=True))
        history = [json.loads(value) for _, value in self.history.items()]
        affected = sorted(
            (item for item in history if (item["timestamp_ms"], item["event_id"]) >= (stamp, event["event_id"])
                and item["timestamp_ms"] <= stamp + 3_600_000),
            key=lambda item: (item["timestamp_ms"], item["event_id"]),
        )
        for item in affected:
            saved = self.predictions.get(item["event_id"])
            prior = json.loads(saved) if saved is not None else None
            revision = prior["window_revision"] + 1 if prior else 1
            prediction, _, elapsed = self.scorer.predict(
                item, history, revision, prior["original_prediction_id"] if prior else None)
            self.inference_us.inc(elapsed)
            if prior and prior["feature_hash"] == prediction["feature_hash"]:
                continue
            self.predictions.put(item["event_id"], json.dumps(prediction, sort_keys=True))
            self.count.inc()
            if prior:
                self.revisions.inc()
            yield json.dumps(prediction, sort_keys=True)
        ctx.timer_service().register_event_time_timer(stamp + 3_600_000 + LATENESS_MS)

    def on_timer(self, timestamp, ctx):
        watermark = ctx.timer_service().current_watermark()
        for identifier, raw in list(self.history.items()):
            event = json.loads(raw)
            if watermark >= event["timestamp_ms"] + 3_600_000 + LATENESS_MS:
                self.history.remove(identifier)
                self.predictions.remove(identifier)
        return []
