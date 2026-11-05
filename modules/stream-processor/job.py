"""Checkpointed PyFlink windows with revision-aware Kafka materialization."""

import json
import os

from pyflink.common import Duration, Row, Time, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import CheckpointingMode, StreamExecutionEnvironment
from pyflink.datastream.checkpoint_config import ExternalizedCheckpointCleanup
from pyflink.datastream.checkpoint_storage import FileSystemCheckpointStorage
from pyflink.datastream.connectors.kafka import (
    DeliveryGuarantee, KafkaOffsetsInitializer, KafkaRecordSerializationSchema, KafkaSink, KafkaSource,
)
from pyflink.datastream.functions import KeyedProcessFunction
from pyflink.datastream.output_tag import OutputTag

from analytics import ClickHouseMap, LATE_TABLE, RISK_TABLE, WINDOWS_TABLE, write_payment_fact
from pyflink.datastream.state import MapStateDescriptor, StateTtlConfig
from pyflink.table import DataTypes, Schema, StreamTableEnvironment
from pyflink.java_gateway import get_gateway

from processing import DEDUP_TTL_MS, LATENESS_MS, TRANSFORMATION, add, decode, dimension, empty_window, output, windows

TOO_LATE = OutputTag("too-late", Types.STRING())


class PaymentWindows(KeyedProcessFunction):
    def open(self, runtime_context):
        descriptor = MapStateDescriptor("seen-event-ids-v1", Types.STRING(), Types.BOOLEAN())
        ttl = StateTtlConfig.new_builder(Time.milliseconds(DEDUP_TTL_MS)) \
            .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite) \
            .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired).build()
        descriptor.enable_time_to_live(ttl)
        self.seen = runtime_context.get_map_state(descriptor)
        self.windows = runtime_context.get_map_state(
            MapStateDescriptor("payment-windows-v1", Types.STRING(), Types.STRING()))
        metrics = runtime_context.get_metrics_group()
        self.late = metrics.counter("late_event_count")
        self.too_late = metrics.counter("too_late_event_count")
        self.duplicates = metrics.counter("duplicate_event_count")
        self.delay = metrics.counter("processing_delay_total_ms")
        self.processed = metrics.counter("processed_event_count")
        self.analytics_enabled = os.environ.get("ANALYTICS_ENABLED") == "true"
        if self.analytics_enabled:
            self.clickhouse_url = os.environ["CLICKHOUSE_URL"].rstrip("/")
            self.clickhouse_user = os.environ["CLICKHOUSE_USER"]
            self.clickhouse_password = os.environ["CLICKHOUSE_PASSWORD"]

    def process_element(self, value, ctx):
        event = decode(value)
        if ctx.timestamp() != event["timestamp_ms"]:
            raise ValueError("Kafka CreateTime must equal the processing contract event_time")
        if self.seen.contains(event["event_id"]):
            self.duplicates.inc()
            return
        watermark = ctx.timer_service().current_watermark()
        accepted = [(start, end) for start, end in windows(event["timestamp_ms"])
                    if watermark < end + LATENESS_MS]
        self.seen.put(event["event_id"], True)
        if self.analytics_enabled:
            write_payment_fact(
                event, self.clickhouse_url, self.clickhouse_user, self.clickhouse_password,
            )
        self.processed.inc()
        self.delay.inc(max(0, ctx.timer_service().current_processing_time() - event["timestamp_ms"]))
        if not accepted:
            self.too_late.inc()
            yield TOO_LATE, json.dumps({
                "event": event, "reason": "all-windows-expired",
                "watermark_ms": watermark, "transformation_id": TRANSFORMATION,
            }, sort_keys=True)
            return
        if event["timestamp_ms"] <= watermark:
            self.late.inc()
        for start, end in accepted:
            key = f"{start}:{end}"
            saved = self.windows.get(key)
            window = add(json.loads(saved) if saved is not None else empty_window(start, end), event)
            if window["emitted"] or watermark >= end - 1:
                window["revision"] += 1
                window["emitted"] = True
                yield json.dumps(output(window, ctx.get_current_key()), sort_keys=True)
            self.windows.put(key, json.dumps(window))
            if watermark < end - 1:
                ctx.timer_service().register_event_time_timer(end - 1)
            ctx.timer_service().register_event_time_timer(end + LATENESS_MS)

    def on_timer(self, timestamp, ctx):
        for key, raw in list(self.windows.items()):
            window = json.loads(raw)
            if timestamp == window["window_end"] - 1 and not window["emitted"]:
                window["emitted"] = True
                window["revision"] += 1
                self.windows.put(key, json.dumps(window))
                yield json.dumps(output(window, ctx.get_current_key()), sort_keys=True)
            if timestamp >= window["window_end"] + LATENESS_MS:
                self.windows.remove(key)


def kafka_properties():
    properties = {
        "bootstrap.servers": os.environ["KAFKA_BROKERS"],
        "transaction.timeout.ms": "900000",
    }
    if os.environ.get("KAFKA_TLS") == "true":
        password = os.environ["KAFKA_PASSWORD"].replace("\\", "\\\\").replace('"', '\\"')
        properties.update({
            "security.protocol": "SASL_SSL", "sasl.mechanism": "SCRAM-SHA-512",
            "sasl.jaas.config": 'org.apache.flink.kafka.shaded.org.apache.kafka.common.security.scram.ScramLoginModule required '
                                f'username="payments" password="{password}";',
            "ssl.truststore.type": "PEM", "ssl.truststore.location": "/etc/kafka/ca.pem",
        })
    return properties


def result_row(value):
    result = json.loads(value)
    return Row(result["result_id"], value)


def preserve_result_sink_identity(env):
    """Bridge the pre-risk SQL sink's generated hashes without discarding its state."""
    java_env = env._j_stream_execution_environment
    transformations = java_env.getTransformations()
    sink = transformations.get(transformations.size() - 1)
    if sink.getClass().getSimpleName() != "SinkTransformation" or sink.getUid() is not None:
        raise ValueError("expected the original unassigned SQL result sink before risk attachment")
    graph = java_env.getStreamGraph(False)
    jvm = get_gateway().jvm
    hashes = jvm.org.apache.flink.streaming.api.graph.StreamGraphHasherV2() \
        .traverseStreamGraphAndGenerateHashes(graph)
    identities = {
        node.getOperatorName(): bytes(value & 255 for value in hashes.get(node.getId())).hex()
        for node in graph.getStreamNodes().toArray()
    }
    custom = jvm.org.apache.flink.streaming.api.datastream.CustomSinkOperatorUidHashes.builder() \
        .setWriterUidHash(identities[sink.getName() + ": Writer"]) \
        .setCommitterUidHash(identities[sink.getName() + ": Committer"]).build()
    # Flink 1.20 accepts custom writer/committer hashes only in the sink constructor.
    replacement = jvm.org.apache.flink.streaming.api.transformations.SinkTransformation(
        sink.getInputStream(), sink.getSink(), sink.getOutputType(), sink.getName(),
        sink.getParallelism(), sink.isParallelismConfigured(), custom)
    replacement.setUid("payment-window-results-sink-v1")
    if sink.getMaxParallelism() > 0:
        replacement.setMaxParallelism(sink.getMaxParallelism())
    replacement.setResources(sink.getMinResources(), sink.getPreferredResources())
    replacement.setBufferTimeout(sink.getBufferTimeout())
    if sink.getDescription() is not None:
        replacement.setDescription(sink.getDescription())
    if sink.getCoLocationGroupKey() is not None:
        replacement.setCoLocationGroupKey(sink.getCoLocationGroupKey())
    if sink.getSlotSharingGroup().isPresent():
        replacement.setSlotSharingGroup(sink.getSlotSharingGroup().get())
    if sink.getChainingStrategy() is not None:
        replacement.setChainingStrategy(sink.getChainingStrategy())
    transformations.set(transformations.size() - 1, replacement)


def build():
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(1)
    env.enable_checkpointing(10_000, CheckpointingMode.EXACTLY_ONCE)
    checkpoints = env.get_checkpoint_config()
    checkpoints.set_checkpoint_storage(FileSystemCheckpointStorage(os.environ["CHECKPOINT_URI"]))
    checkpoints.set_min_pause_between_checkpoints(2_000)
    checkpoints.set_checkpoint_timeout(120_000)
    checkpoints.enable_externalized_checkpoints(ExternalizedCheckpointCleanup.RETAIN_ON_CANCELLATION)
    properties = kafka_properties()
    source = KafkaSource.builder().set_topics("payment-processing-events-v1") \
        .set_group_id("payment-windows-v1").set_starting_offsets(KafkaOffsetsInitializer.earliest()) \
        .set_value_only_deserializer(SimpleStringSchema()).set_properties(properties).build()
    # Native Kafka split watermarks use CreateTime, validated against event_time above.
    stream = env.from_source(source, WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_seconds(10))
        .with_idleness(Duration.of_seconds(30)),
        "synthetic-payment-processing", type_info=Types.STRING()).uid("processing-source-v1")
    late_builder = KafkaSink.builder().set_bootstrap_servers(properties["bootstrap.servers"])
    for key, value in properties.items():
        late_builder.set_property(key, value)
    late_sink = late_builder.set_delivery_guarantee(DeliveryGuarantee.AT_LEAST_ONCE) \
        .set_record_serializer(KafkaRecordSerializationSchema.builder().set_topic("payment-events-too-late")
            .set_value_serialization_schema(SimpleStringSchema()).build()).build()
    windowed = stream.key_by(lambda value: dimension(decode(value)), key_type=Types.STRING()) \
        .process(PaymentWindows(), output_type=Types.STRING()).name("payment-windows").uid("payment-windows-v1")
    windowed.get_side_output(TOO_LATE).sink_to(late_sink).uid("too-late-sink-v1")
    aggregates = windowed
    if os.environ.get("ANALYTICS_ENABLED") == "true":
        aggregates = aggregates.map(ClickHouseMap(WINDOWS_TABLE), output_type=Types.STRING()) \
            .name("clickhouse-window-aggregates").uid("clickhouse-window-aggregates-v1")
    table_env = StreamTableEnvironment.create(env)
    rows = aggregates.map(result_row, output_type=Types.ROW_NAMED(["result_id", "payload"], [Types.STRING(), Types.STRING()])) \
        .uid("result-rows-v1")
    table = table_env.from_data_stream(rows, Schema.new_builder()
        .column("result_id", DataTypes.STRING().not_null())
        .column("payload", DataTypes.STRING()).build())
    table_env.create_temporary_view("window_results", table)
    options = {
        "connector": "upsert-kafka", "topic": "payment-window-results-v1",
        "key.format": "raw", "value.format": "json",
        **{f"properties.{key}": value for key, value in properties.items()},
    }
    # SQL option values may contain SASL credentials; never log this statement.
    rendered = ", ".join(f"'{key}' = '{value.replace(chr(39), chr(39) * 2)}'" for key, value in options.items())
    table_env.execute_sql("CREATE TABLE results (result_id STRING, payload STRING, "
        "PRIMARY KEY (result_id) NOT ENFORCED) WITH (" + rendered + ")")
    statement = table_env.create_statement_set()
    statement.add_insert_sql("INSERT INTO results SELECT result_id, payload FROM window_results")
    statement.attach_as_datastream()
    preserve_result_sink_identity(env)
    # Optional branches must not affect the baseline graph used by the state-identity bridge.
    analytics_enabled = os.environ.get("ANALYTICS_ENABLED") == "true"
    if analytics_enabled:
        # Stateless side branches: Kafka sinks keep their uids and savepoints restore unchanged.
        clickhouse_branch(windowed, LATE_TABLE, "clickhouse-late-events", TOO_LATE)
    if os.environ.get("RISK_ENABLED") == "true":
        from risk import RISK_TOO_LATE, RiskPayments, account_currency

        predictions = stream.key_by(account_currency, key_type=Types.STRING()).process(
            RiskPayments(), output_type=Types.STRING()).name("advisory-risk-predictions").uid("risk-predictions-v1")
        for output, topic, uid in (
            (predictions, "payment-risk-predictions-v1", "risk-audit-sink-v1"),
            (predictions.get_side_output(RISK_TOO_LATE), "payment-risk-too-late-v1", "risk-late-sink-v1"),
        ):
            builder = KafkaSink.builder().set_bootstrap_servers(properties["bootstrap.servers"])
            for key, value in properties.items():
                builder.set_property(key, value)
            sink = builder.set_delivery_guarantee(DeliveryGuarantee.AT_LEAST_ONCE) \
                .set_record_serializer(KafkaRecordSerializationSchema.builder().set_topic(topic)
                    .set_value_serialization_schema(SimpleStringSchema()).build()).build()
            output.sink_to(sink).uid(uid)
        if analytics_enabled:
            clickhouse_branch(predictions, RISK_TABLE, "clickhouse-risk-predictions")
            clickhouse_branch(predictions, LATE_TABLE, "clickhouse-risk-late-events", RISK_TOO_LATE)
    return env


def clickhouse_branch(stream, table, name, side_output=None):
    # Resolving a side output here keeps it out of the graph when the branch is skipped.
    if side_output is not None:
        stream = stream.get_side_output(side_output)
    return stream.map(ClickHouseMap(table), output_type=Types.STRING()).name(name).uid(f"{name}-v1")


def run():
    build().execute("payment-event-time-v1")


if __name__ == "__main__":
    run()
