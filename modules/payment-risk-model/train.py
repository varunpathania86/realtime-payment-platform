"""Reproduce synthetic account-held-out training and Python/ONNX validation."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import time
import zipfile
from uuid import NAMESPACE_URL, uuid5

from features import CONTRACT, feature_vector

SEED = 731


def dataset(accounts=80, payments=40):
    rng = random.Random(SEED)
    rows, labels, groups = [], [], []
    base = datetime(2026, 11, 4, tzinfo=timezone.utc)
    for account in range(accounts):
        history = []
        account_id = str(uuid5(NAMESPACE_URL, f"risk-training/account/{account}"))
        age_days = rng.choice([1, 3, 10, 90, 365])
        initial_device = str(uuid5(NAMESPACE_URL, f"risk-training/device/{account}"))
        for index in range(payments):
            stamp = base + timedelta(seconds=index * 25)
            changed = rng.random() < 0.12
            event = {
                "event_id": str(uuid5(NAMESPACE_URL, f"risk-training/{account}/{index}")),
                "account_id": account_id, "event_time": stamp.isoformat().replace("+00:00", "Z"),
                "account_created_at": (base - timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
                    if rng.random() > 0.1 else None,
                "device_id": str(uuid5(NAMESPACE_URL, f"risk-training/device/{account}/{index}"))
                    if changed else initial_device,
                "amount_minor": max(1, int(rng.lognormvariate(7.5, 1.2))),
                "currency": rng.choice(["EUR", "USD", "GBP"]),
                "country": rng.choice(["DE", "FR", "NL"]) if changed else "DE",
                "payment_method": rng.choice(["card", "transfer", "wallet"]),
                "outcome": "rejected" if rng.random() < 0.1 else "authorized",
            }
            if rng.random() < 0.1:
                event["device_id"] = None
            vector = feature_vector(event, history)
            logit = (
                -4.5 + 0.00006 * vector["payment_amount_minor"]
                + 1.4 * (vector["device_changed"] == 1)
                + 0.8 * (vector["country_changed"] == 1)
                + 1.0 * (vector["account_age_days"] is not None and vector["account_age_days"] < 7)
                + 0.12 * vector["rejections_last_10_minutes"]
            )
            labels.append(int(rng.random() < 1 / (1 + math.exp(-logit))))
            rows.append(vector)
            groups.append(account)
            history.append(event)
    return rows, labels, groups


def pipeline(classifier):
    import numpy as np
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    return Pipeline([
        ("features", ColumnTransformer([
            ("numeric", Pipeline([
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
            ]), CONTRACT["numeric"]),
            ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype=np.float32),
                CONTRACT["categorical"]),
        ], sparse_threshold=0)),
        ("classifier", classifier),
    ])


def inputs(frame):
    import numpy as np

    return {
        name: frame[[name]].to_numpy(dtype=np.float32 if name in CONTRACT["numeric"] else str)
        for name in CONTRACT["numeric"] + CONTRACT["categorical"]
    }


def export(model):
    from lightgbm import LGBMClassifier
    from onnxmltools.convert.lightgbm.operator_converters.LightGbm import convert_lightgbm
    from skl2onnx import convert_sklearn, update_registered_converter
    from skl2onnx.common.data_types import FloatTensorType, StringTensorType
    from skl2onnx.common.shape_calculator import calculate_linear_classifier_output_shapes

    update_registered_converter(
        LGBMClassifier, "LightGbmLGBMClassifier", calculate_linear_classifier_output_shapes,
        convert_lightgbm, options={"nocl": [True, False], "zipmap": [True, False, "columns"]},
    )
    initial = [
        (name, FloatTensorType([None, 1]) if name in CONTRACT["numeric"] else StringTensorType([None, 1]))
        for name in CONTRACT["numeric"] + CONTRACT["categorical"]
    ]
    return convert_sklearn(
        model, initial_types=initial, target_opset={"": 15, "ai.onnx.ml": 3},
        options={
            id(model.named_steps["classifier"]): {"zipmap": False},
            id(model.named_steps["features"].named_transformers_["numeric"].named_steps["scale"]): {"div": "div"},
        },
    )


def metrics(labels, scores):
    from sklearn.metrics import (
        average_precision_score, confusion_matrix, f1_score, precision_score,
        recall_score, roc_auc_score,
    )

    predicted = scores >= 0.5
    tn, fp, fn, tp = confusion_matrix(labels, predicted, labels=[0, 1]).ravel()
    return {
        "threshold": 0.5, "precision": precision_score(labels, predicted, zero_division=0),
        "recall": recall_score(labels, predicted, zero_division=0),
        "f1": f1_score(labels, predicted, zero_division=0),
        "pr_auc": average_precision_score(labels, scores), "roc_auc": roc_auc_score(labels, scores),
        "false_positive_rate": fp / (fp + tn), "false_negative_rate": fn / (fn + tp),
    }


def train(output):
    import importlib.metadata
    import numpy as np
    import onnx
    import onnxruntime as ort
    import pandas as pd
    from lightgbm import LGBMClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupShuffleSplit

    rows, labels, groups = dataset()
    frame = pd.DataFrame(rows)
    for name in CONTRACT["numeric"]:
        frame[name] = frame[name].astype(np.float32)
    labels = np.asarray(labels)
    train_ids, test_ids = next(GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=SEED)
        .split(frame, labels, groups))
    if set(np.asarray(groups)[train_ids]) & set(np.asarray(groups)[test_ids]):
        raise ValueError("account leakage between train and held-out data")
    models = {
        "logistic-regression": LogisticRegression(max_iter=1000, random_state=SEED),
        "lightgbm": LGBMClassifier(n_estimators=60, num_leaves=7, random_state=SEED,
            n_jobs=1, deterministic=True, force_col_wise=True, verbosity=-1),
    }
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "seed": SEED, "rows": len(rows), "train_rows": len(train_ids), "held_out_rows": len(test_ids),
        "positive_fraction": float(labels.mean()), "split": "held-out accounts; no tuning on held-out set",
        "selected_model": "logistic-regression",
        "selection_policy": "baseline remains selected; comparison does not automatically promote a model",
        "models": {},
    }
    golden = frame.iloc[test_ids[:16]].copy()
    golden.loc[golden.index[0], "payment_amount_minor"] = np.nan
    golden.loc[golden.index[0], "country"] = "ZZ"
    selected_scores = None
    for name, classifier in models.items():
        fitted = pipeline(classifier).fit(frame.iloc[train_ids], labels[train_ids])
        exported = export(fitted)
        onnx.checker.check_model(exported)
        data = exported.SerializeToString()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(data, sess_options=options, providers=["CPUExecutionProvider"])
        expected = fitted.predict_proba(golden)[:, 1]
        actual = session.run(None, inputs(golden))[1][:, 1]
        held_out = frame.iloc[test_ids]
        held_out_expected = fitted.predict_proba(held_out)[:, 1]
        held_out_actual = session.run(None, inputs(held_out))[1][:, 1]
        error = float(max(np.max(np.abs(expected - actual)),
            np.max(np.abs(held_out_expected - held_out_actual))))
        if error > CONTRACT["onnx_parity_absolute_tolerance"]:
            raise ValueError(f"{name}: ONNX parity error {error}")
        latency = []
        for _ in range(10):
            session.run(None, inputs(golden.iloc[:1]))
        one = inputs(golden.iloc[:1])
        for _ in range(100):
            started = time.perf_counter()
            session.run(None, one)
            latency.append((time.perf_counter() - started) * 1000)
        result = metrics(labels[test_ids], held_out_expected)
        result.update({
            "model_bytes": len(data), "onnx_max_absolute_error": error,
            "inference_latency_ms": {
                "p50": float(np.percentile(latency, 50)), "p95": float(np.percentile(latency, 95)),
                "p99": float(np.percentile(latency, 99)), "samples": 100,
                "scope": "warmed one-row ONNX Runtime CPU session; excludes feature extraction and IO",
            },
        })
        report["models"][name] = result
        (output / (name + ".onnx")).write_bytes(data)
        if name == report["selected_model"]:
            (output / "model.onnx").write_bytes(data)
            selected_scores = actual
    golden_rows = golden.astype(object).where(pd.notna(golden), None).to_dict(orient="records")
    golden_artifact = {
        "feature_version": CONTRACT["feature_version"], "rows": golden_rows,
        "expected_scores": selected_scores.tolist(),
        "absolute_tolerance": CONTRACT["onnx_parity_absolute_tolerance"],
    }
    metadata = {
        **CONTRACT, "selected_model": report["selected_model"],
        "model_sha256": hashlib.sha256((output / "model.onnx").read_bytes()).hexdigest(),
        "runtime": {name: importlib.metadata.version(name) for name in (
            "numpy", "pandas", "scikit-learn", "lightgbm", "onnx", "onnxruntime", "skl2onnx", "onnxmltools",
        )},
    }
    for name, value in (("metadata.json", metadata), ("metrics.json", report), ("golden.json", golden_artifact)):
        (output / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    (output / "model-card.md").write_text(
        "# Synthetic payment risk model\n\n"
        "Advisory learning artifact, never an authorization or fraud-blocking decision.\n\n"
        "Deterministic generated synthetic data with intentionally constructed risk patterns; "
        "metrics do not estimate real-world fraud performance. Account-held-out split, "
        "prior-event features only, no current outcome label leakage. Missing account/device "
        "context is unknown, not safe. Logistic regression stays the deployed baseline; "
        "LightGBM is comparison only. Threshold 0.5 is an evaluation convention, not a "
        "payment policy. Country/device flags are descriptive, not causal explanations.\n\n"
        "Generated ONNX and metadata belong in MinIO, not Git. Runtime publication and "
        "Flink parity/revision acceptance must pass separately before Stage 7 is complete.\n"
    )
    with zipfile.ZipFile(output / "payment_risk.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        contents = {
            "__init__.py": b"",
            "features.py": Path(__file__).with_name("features.py").read_bytes(),
            "scorer.py": Path(__file__).with_name("scorer.py").read_bytes(),
            "feature_contract.json": Path(__file__).with_name("feature_contract.json").read_bytes(),
            **{name: (output / name).read_bytes() for name in ("model.onnx", "metadata.json", "golden.json")},
        }
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo("payment_risk/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    manifest = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir()) if path.is_file() and path.name != "manifest.json"
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    train(parser.parse_args().output)
