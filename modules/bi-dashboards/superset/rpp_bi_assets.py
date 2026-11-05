"""Declarative Superset assets for the payment-operations dashboard.

Pure Python so tests can validate the bundle offline. UUIDs derive from stable
names, so repeated imports update the same objects instead of creating copies.
"""

import json
import uuid

import yaml

NAMESPACE = uuid.UUID("6f1d4c7e-2b7a-4f53-9a51-0f6c2d7b9e10")
EXPORT_VERSION = "1.0.0"
DASHBOARD_SLUG = "payment-operations"
MANUAL_REVIEW_THRESHOLD = 0.5
CLICKHOUSE = "ClickHouse realtime analytics"
TRINO = "Trino lakehouse history"


def stable_uuid(name):
    return str(uuid.uuid5(NAMESPACE, name))


DASHBOARD_UUID = stable_uuid("dashboard/payment-operations")
EMBEDDED_UUID = stable_uuid("embedded/payment-operations")


def column(name, kind, temporal=False):
    return {
        "column_name": name, "type": kind, "is_dttm": temporal, "is_active": True,
        "groupby": True, "filterable": True, "expression": None, "description": None,
        "verbose_name": None, "python_date_format": None, "advanced_data_type": None, "extra": None,
    }


DATASETS = {
    "payment_operations": {
        "database": CLICKHOUSE,
        "sql": (
            "SELECT event_time, currency, country, payment_method, outcome,\n"
            "       amount_minor / 100.0 AS amount,\n"
            "       greatest(dateDiff('millisecond', event_time, processed_at), 0) AS processing_delay_ms\n"
            "FROM analytics.payment_facts FINAL"
        ),
        "time_column": "event_time",
        "columns": [
            column("event_time", "DateTime64(3, 'UTC')", True), column("currency", "String"),
            column("country", "String"), column("payment_method", "String"), column("outcome", "String"),
            column("amount", "Float64"), column("processing_delay_ms", "Int64"),
        ],
    },
    "payment_late_events": {
        "database": CLICKHOUSE,
        "sql": (
            "SELECT event_time, reason, job_name, currency, payment_method\n"
            "FROM analytics.payment_late_events FINAL"
        ),
        "time_column": "event_time",
        "columns": [
            column("event_time", "DateTime64(3, 'UTC')", True), column("reason", "String"),
            column("job_name", "String"), column("currency", "String"), column("payment_method", "String"),
        ],
    },
    "payment_risk_predictions": {
        "database": CLICKHOUSE,
        "sql": (
            "SELECT event_time, model_name, model_version, risk_score,\n"
            "       toString(floor(risk_score * 10) / 10) AS score_bucket,\n"
            f"       risk_score >= {MANUAL_REVIEW_THRESHOLD} AS manual_review\n"
            "FROM analytics.payment_risk_predictions FINAL"
        ),
        "time_column": "event_time",
        "columns": [
            column("event_time", "DateTime64(3, 'UTC')", True), column("model_name", "String"),
            column("model_version", "String"), column("risk_score", "Float64"),
            column("score_bucket", "String"), column("manual_review", "Bool"),
        ],
    },
    "lakehouse_payment_history": {
        "database": TRINO,
        "sql": (
            "SELECT event_time, currency, outcome, amount_minor / 100.0 AS amount\n"
            "FROM payments.payment_events"
        ),
        "time_column": "event_time",
        "columns": [
            column("event_time", "TIMESTAMP(6) WITH TIME ZONE", True), column("currency", "VARCHAR"),
            column("outcome", "VARCHAR"), column("amount", "DOUBLE"),
        ],
    },
}


def metric(expression, label):
    return {
        "expressionType": "SQL", "sqlExpression": expression, "label": label,
        "hasCustomLabel": True, "optionName": "metric_" + label.lower().replace(" ", "_"),
    }


def big_number(dataset, value, label, number_format):
    return dataset, "big_number_total", {"metric": metric(value, label), "y_axis_format": number_format}


def series(dataset, kind, value, label, x_axis, groupby=(), time_grain=None):
    params = {
        "x_axis": x_axis, "metrics": [metric(value, label)], "groupby": list(groupby),
        "row_limit": 10000, "show_legend": True, "rich_tooltip": True, "y_axis_format": "SMART_NUMBER",
    }
    if time_grain:
        params["time_grain_sqla"] = time_grain
    return dataset, kind, params


def pie(dataset, value, label, groupby):
    return dataset, "pie", {
        "metric": metric(value, label), "groupby": [groupby], "row_limit": 100,
        "show_labels": True, "show_legend": True, "label_type": "key_percent", "number_format": "SMART_NUMBER",
    }


# (key, title, dataset, viz_type, params, width) in dashboard order; grid rows are 12 units wide.
CHARTS = [
    ("authorization-rate", "Authorization rate", *big_number(
        "payment_operations", "countIf(outcome = 'authorized') / greatest(count(), 1)", "Authorization rate", ".1%"), 3),
    ("rejected-payments", "Rejected payments", *big_number(
        "payment_operations", "countIf(outcome = 'rejected')", "Rejected payments", ",d"), 3),
    ("manual-review-volume", "Manual-review volume (advisory, score >= 0.5)", *big_number(
        "payment_risk_predictions", "countIf(manual_review)", "Manual-review volume", ",d"), 3),
    ("late-event-total", "Late events", *big_number(
        "payment_late_events", "count()", "Late events", ",d"), 3),
    ("amount-by-currency", "Amount by currency", *pie(
        "payment_operations", "sum(amount)", "Amount", "currency"), 4),
    ("processing-delay", "Processing delay p95 (ms)", *series(
        "payment_operations", "echarts_timeseries_line", "quantile(0.95)(processing_delay_ms)",
        "p95 delay ms", "event_time", time_grain="PT1M"), 8),
    ("late-events-by-reason", "Late events by reason", *series(
        "payment_late_events", "echarts_timeseries_bar", "count()", "Late events", "event_time",
        groupby=("reason",), time_grain="PT1H"), 6),
    ("risk-score-distribution", "Risk-score distribution", *series(
        "payment_risk_predictions", "echarts_timeseries_bar", "count()", "Predictions", "score_bucket"), 6),
    ("model-version-usage", "Model version usage", *pie(
        "payment_risk_predictions", "count()", "Predictions", "model_version"), 4),
    ("lakehouse-daily-amount", "Iceberg history: daily amount by currency", *series(
        "lakehouse_payment_history", "echarts_timeseries_bar", "sum(amount)", "Amount", "event_time",
        groupby=("currency",), time_grain="P1D"), 8),
]
ROWS = [
    ["authorization-rate", "rejected-payments", "manual-review-volume", "late-event-total"],
    ["amount-by-currency", "processing-delay"],
    ["late-events-by-reason", "risk-score-distribution"],
    ["model-version-usage", "lakehouse-daily-amount"],
]


def database_config(name, uri, extra=None):
    return {
        "database_name": name, "sqlalchemy_uri": uri, "uuid": stable_uuid(f"database/{name}"),
        "version": EXPORT_VERSION, "cache_timeout": None, "expose_in_sqllab": False,
        "allow_run_async": False, "allow_ctas": False, "allow_cvas": False, "allow_dml": False,
        "allow_file_upload": False, "allow_csv_upload": False,
        "extra": {"allows_virtual_table_explore": True, **(extra or {})},
    }


def dataset_config(key, spec, database_uuid):
    return {
        "table_name": key, "schema": None, "catalog": None, "sql": spec["sql"],
        "main_dttm_col": spec["time_column"], "uuid": stable_uuid(f"dataset/{key}"),
        "database_uuid": database_uuid, "version": EXPORT_VERSION, "description": None,
        "default_endpoint": None, "offset": 0, "cache_timeout": None, "filter_select_enabled": True,
        "fetch_values_predicate": None, "extra": None, "normalize_columns": False,
        "always_filter_main_dttm": False, "metrics": [], "columns": spec["columns"],
    }


def chart_queries(viz_type, params):
    """Return the query fields the Superset frontend sends for this chart.

    Superset compares guest chart-data requests against the saved chart's query_context; charts
    saved through the UI store one, so imported charts must too or embedded guests are rejected.
    """
    metric_ = params["metric"] if "metric" in params else params["metrics"][0]
    if viz_type == "big_number_total":
        return [{"metrics": [metric_], "columns": [], "orderby": None}]
    columns = list(params["groupby"])
    if "x_axis" in params:
        axis = {"columnType": "BASE_AXIS", "expressionType": "SQL", "isColumnReference": True,
                "label": params["x_axis"], "sqlExpression": params["x_axis"]}
        if params.get("time_grain_sqla"):
            axis["timeGrain"] = params["time_grain_sqla"]
        columns.insert(0, axis)
    return [{"metrics": [metric_], "columns": columns, "orderby": [[metric_, False]]}]


def chart_config(key, title, dataset, viz_type, params):
    return {
        "slice_name": title, "description": None, "certified_by": None, "certification_details": None,
        "viz_type": viz_type, "params": {
            "viz_type": viz_type, "adhoc_filters": [], "time_range": "No filter", **params,
        },
        "query_context": json.dumps({"queries": chart_queries(viz_type, params)}, sort_keys=True),
        "cache_timeout": None, "uuid": stable_uuid(f"chart/{key}"),
        "dataset_uuid": stable_uuid(f"dataset/{dataset}"), "version": EXPORT_VERSION,
    }


def dashboard_position(charts, rows):
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": "Payment operations"}},
    }
    by_key = {item[0]: item for item in charts}
    for index, row in enumerate(rows):
        row_id = f"ROW-payment-operations-{index}"
        position["GRID_ID"]["children"].append(row_id)
        position[row_id] = {
            "type": "ROW", "id": row_id, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        for key in row:
            chart_id = f"CHART-{key}"
            position[row_id]["children"].append(chart_id)
            position[chart_id] = {
                "type": "CHART", "id": chart_id, "children": [], "parents": ["ROOT_ID", "GRID_ID", row_id],
                # chartId is a placeholder; import remaps it to the database id through the uuid.
                "meta": {
                    "uuid": stable_uuid(f"chart/{key}"), "chartId": list(by_key).index(key) + 1,
                    "width": by_key[key][5], "height": 50, "sliceName": by_key[key][1],
                },
            }
    return position


def bundle(clickhouse_uri, trino_uri=None):
    """Return import-ready YAML contents and the database password map keys."""
    charts = [item for item in CHARTS if trino_uri or DATASETS[item[2]]["database"] != TRINO]
    keys = {item[0] for item in charts}
    rows = [[key for key in row if key in keys] for row in ROWS]
    rows = [row for row in rows if row]
    databases = {CLICKHOUSE: database_config(CLICKHOUSE, clickhouse_uri)}
    if trino_uri:
        databases[TRINO] = database_config(TRINO, trino_uri)
    contents = {"metadata.yaml": yaml.safe_dump({
        "version": EXPORT_VERSION, "type": "Dashboard", "timestamp": "2026-01-01T00:00:00+00:00",
    })}
    for name, config in databases.items():
        contents[f"databases/{database_file(name)}.yaml"] = yaml.safe_dump(config, sort_keys=True)
    for key, spec in DATASETS.items():
        if spec["database"] in databases:
            config = dataset_config(key, spec, databases[spec["database"]]["uuid"])
            contents[f"datasets/{database_file(spec['database'])}/{key}.yaml"] = yaml.safe_dump(config, sort_keys=True)
    for key, title, dataset, viz_type, params, _width in charts:
        contents[f"charts/{key}.yaml"] = yaml.safe_dump(
            chart_config(key, title, dataset, viz_type, params), sort_keys=True)
    contents["dashboards/payment-operations.yaml"] = yaml.safe_dump({
        "dashboard_title": "Payment operations", "description": None, "css": "",
        "slug": DASHBOARD_SLUG, "uuid": DASHBOARD_UUID, "published": True,
        "certified_by": None, "certification_details": None, "version": EXPORT_VERSION,
        "position": dashboard_position(charts, rows),
        "metadata": {
            "color_scheme": "supersetColors", "refresh_frequency": 60, "timed_refresh_immune_slices": [],
            "expanded_slices": {}, "label_colors": {}, "shared_label_colors": [],
            "native_filter_configuration": [], "cross_filters_enabled": False,
        },
    }, sort_keys=True)
    return contents


def database_file(name):
    return name.lower().replace(" ", "_")
