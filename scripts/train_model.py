from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rto_guard.config import load_config, resolve_path  # noqa: E402
from rto_guard.decisions import bucket_for_risk  # noqa: E402
from rto_guard.economics import estimate_order_impact  # noqa: E402
from rto_guard.features import CATEGORICAL_FEATURES, engineer_features  # noqa: E402


def normalize_data(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    required = {
        "order_id", "order_datetime", "customer_name", "phone", "address", "city", "state",
        "pincode", "order_value", "items", "num_items", "customer_prior_orders",
        "customer_prior_rto", "utm_source", "courier", "payment_method", "final_status",
    }
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Input CSV is missing columns: {', '.join(sorted(missing))}")

    before = len(raw)
    duplicate_rows = int(raw.duplicated().sum())
    clean = raw.drop_duplicates().copy()
    clean["final_status_normalized"] = clean["final_status"].astype(str).str.strip().str.upper()
    status_counts = clean["final_status_normalized"].value_counts(dropna=False).to_dict()
    unresolved = int((~clean["final_status_normalized"].isin(["DELIVERED", "RTO"])).sum())
    clean = clean[clean["final_status_normalized"].isin(["DELIVERED", "RTO"])].copy()
    clean["target_rto"] = (clean["final_status_normalized"] == "RTO").astype(int)
    clean["order_datetime_parsed"] = pd.to_datetime(clean["order_datetime"], errors="coerce")
    clean = clean[clean["order_datetime_parsed"].notna()].copy()

    prior_orders = pd.to_numeric(clean["customer_prior_orders"], errors="coerce")
    prior_rto = pd.to_numeric(clean["customer_prior_rto"], errors="coerce")
    inconsistent_history = (prior_orders < 0) | (prior_rto < 0) | (prior_rto > prior_orders)

    audit = {
        "raw_rows": int(before),
        "exact_duplicate_rows_removed": duplicate_rows,
        "rows_after_deduplication": int(before - duplicate_rows),
        "normalized_status_counts_before_exclusion": {str(k): int(v) for k, v in status_counts.items()},
        "unresolved_status_rows_excluded": unresolved,
        "clean_labeled_rows": int(len(clean)),
        "delivered_rows": int((clean["target_rto"] == 0).sum()),
        "rto_rows": int(clean["target_rto"].sum()),
        "rto_rate": float(clean["target_rto"].mean()),
        "date_min": clean["order_datetime_parsed"].min().strftime("%Y-%m-%d"),
        "date_max": clean["order_datetime_parsed"].max().strftime("%Y-%m-%d"),
        "zero_or_negative_order_value_rows": int((pd.to_numeric(clean["order_value"], errors="coerce").fillna(0) <= 0).sum()),
        "inconsistent_prior_history_rows": int(inconsistent_history.sum()),
        "sample_aov_mean_inr": float(clean["order_value"].mean()),
        "sample_aov_median_inr": float(clean["order_value"].median()),
    }
    return clean, audit


def segment_table(df: pd.DataFrame, by: str, min_n: int = 1) -> list[dict]:
    grouped = df.groupby(by, dropna=False).agg(
        orders=("target_rto", "size"),
        rto_orders=("target_rto", "sum"),
        rto_rate=("target_rto", "mean"),
    ).reset_index()
    grouped = grouped[grouped["orders"] >= min_n].sort_values("orders", ascending=False)
    records = []
    for row in grouped.to_dict("records"):
        records.append({
            by: "Unknown" if pd.isna(row[by]) else str(row[by]),
            "orders": int(row["orders"]),
            "rto_orders": int(row["rto_orders"]),
            "rto_rate": round(float(row["rto_rate"]), 4),
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate the RTO Guard COD-risk model.")
    parser.add_argument("--input", required=True, help="Path to the supplied orders_sample.csv")
    args = parser.parse_args()

    config = load_config()
    raw = pd.read_csv(args.input)
    clean, audit = normalize_data(raw)
    features = engineer_features(clean)
    target = clean["target_rto"].astype(int)
    dates = clean["order_datetime_parsed"]
    train_mask = dates < pd.Timestamp("2026-09-01")
    selection_train_mask = dates < pd.Timestamp("2026-08-01")
    selection_valid_mask = (dates >= pd.Timestamp("2026-08-01")) & (dates < pd.Timestamp("2026-09-01"))
    test_mask = (dates >= pd.Timestamp("2026-09-01")) & (dates < pd.Timestamp("2026-10-01"))
    if train_mask.sum() == 0 or selection_train_mask.sum() == 0 or selection_valid_mask.sum() == 0 or test_mask.sum() == 0:
        raise ValueError("Expected April-July selection training, August early-stopping validation and September 2026 temporal holdout.")

    selection_model = CatBoostClassifier(
        iterations=1000,
        depth=4,
        learning_rate=0.025,
        loss_function="Logloss",
        eval_metric="AUC",
        l2_leaf_reg=10,
        random_strength=1,
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
    )
    selection_model.fit(
        features.loc[selection_train_mask], target.loc[selection_train_mask],
        cat_features=CATEGORICAL_FEATURES,
        eval_set=(features.loc[selection_valid_mask], target.loc[selection_valid_mask]),
        early_stopping_rounds=100,
        verbose=False,
    )
    selected_iterations = max(int(selection_model.best_iteration_) + 1, 100)

    # Refit on all April-August labelled orders using an iteration count selected
    # without touching the September temporal holdout.
    validation_model = CatBoostClassifier(
        iterations=selected_iterations,
        depth=4,
        learning_rate=0.025,
        loss_function="Logloss",
        l2_leaf_reg=10,
        random_strength=1,
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
    )
    validation_model.fit(
        features.loc[train_mask], target.loc[train_mask],
        cat_features=CATEGORICAL_FEATURES,
        verbose=False,
    )
    test_prob = validation_model.predict_proba(features.loc[test_mask])[:, 1]
    test = clean.loc[test_mask].copy()
    test["risk_score"] = test_prob
    test["bucket"] = [bucket_for_risk(p, config["risk_thresholds"]) for p in test_prob]
    for metric_col in ["rto_cost_avoided_inr", "genuine_margin_lost_inr", "prepaid_discount_cost_inr", "net_impact_inr"]:
        test[metric_col] = [
            estimate_order_impact(p, bucket, config["unit_economics"])[metric_col]
            for p, bucket in zip(test["risk_score"], test["bucket"])
        ]

    y_test = target.loc[test_mask]
    metrics = {
        "temporal_validation": {
            "train_period": "2026-04-01 to 2026-08-31",
            "train_orders": int(train_mask.sum()),
            "iteration_selection_train_orders": int(selection_train_mask.sum()),
            "iteration_selection_train_period": "2026-04-01 to 2026-07-31",
            "iteration_selection_validation_orders": int(selection_valid_mask.sum()),
            "iteration_selection_validation_period": "2026-08-01 to 2026-08-31",
            "holdout_period": "2026-09-01 to 2026-09-30",
            "holdout_orders": int(test_mask.sum()),
            "holdout_rto_rate": round(float(y_test.mean()), 4),
            "roc_auc": round(float(roc_auc_score(y_test, test_prob)), 4),
            "average_precision": round(float(average_precision_score(y_test, test_prob)), 4),
            "brier_score": round(float(brier_score_loss(y_test, test_prob)), 4),
            "selected_iterations": int(selected_iterations),
        },
        "data_audit": audit,
        "thresholds": config["risk_thresholds"],
        "feature_importance": sorted(
            [
                {"feature": name, "importance": round(float(importance), 2)}
                for name, importance in zip(features.columns, validation_model.feature_importances_)
            ],
            key=lambda item: item["importance"], reverse=True,
        ),
    }

    bucket_summary = test.groupby("bucket", observed=False).agg(
        orders=("target_rto", "size"),
        observed_rto_orders=("target_rto", "sum"),
        observed_rto_rate=("target_rto", "mean"),
        mean_model_score=("risk_score", "mean"),
        rto_cost_avoided_inr=("rto_cost_avoided_inr", "sum"),
        genuine_margin_lost_inr=("genuine_margin_lost_inr", "sum"),
        prepaid_discount_cost_inr=("prepaid_discount_cost_inr", "sum"),
        net_impact_inr=("net_impact_inr", "sum"),
    ).reindex(["Ship as-is", "Ship after confirmation", "Nudge to prepaid", "Hold / cancel"])
    metrics["holdout_bucket_summary"] = [
        {
            "bucket": str(bucket),
            "orders": int(row["orders"]) if pd.notna(row["orders"]) else 0,
            "observed_rto_orders": int(row["observed_rto_orders"]) if pd.notna(row["observed_rto_orders"]) else 0,
            "observed_rto_rate": round(float(row["observed_rto_rate"]), 4) if pd.notna(row["observed_rto_rate"]) else None,
            "mean_model_score": round(float(row["mean_model_score"]), 4) if pd.notna(row["mean_model_score"]) else None,
            "rto_cost_avoided_inr": round(float(row["rto_cost_avoided_inr"]), 2) if pd.notna(row["rto_cost_avoided_inr"]) else 0,
            "genuine_margin_lost_inr": round(float(row["genuine_margin_lost_inr"]), 2) if pd.notna(row["genuine_margin_lost_inr"]) else 0,
            "prepaid_discount_cost_inr": round(float(row["prepaid_discount_cost_inr"]), 2) if pd.notna(row["prepaid_discount_cost_inr"]) else 0,
            "net_impact_inr": round(float(row["net_impact_inr"]), 2) if pd.notna(row["net_impact_inr"]) else 0,
        }
        for bucket, row in bucket_summary.iterrows()
    ]
    metrics["scenario_impact"] = {
        "rto_cost_avoided_inr": round(float(test["rto_cost_avoided_inr"].sum()), 2),
        "genuine_margin_lost_inr": round(float(test["genuine_margin_lost_inr"].sum()), 2),
        "prepaid_discount_cost_inr": round(float(test["prepaid_discount_cost_inr"].sum()), 2),
        "net_impact_inr": round(float(test["net_impact_inr"].sum()), 2),
        "note": "Scenario estimate versus ship-as-is; based on explicit assumptions in config.yaml, not measured experiment results.",
    }
    metrics["segments"] = {
        "state": segment_table(clean, "state"),
        "city": segment_table(clean, "city"),
        "utm_source": segment_table(clean, "utm_source"),
        "customer_prior_rto": segment_table(clean, "customer_prior_rto"),
    }

    output_model = resolve_path(config["app"]["model_path"])
    output_model.parent.mkdir(parents=True, exist_ok=True)
    final_model = CatBoostClassifier(
        iterations=selected_iterations,
        depth=4, learning_rate=0.025, loss_function="Logloss", l2_leaf_reg=10,
        random_strength=1, random_seed=42, verbose=False, allow_writing_files=False,
    )
    final_model.fit(features, target, cat_features=CATEGORICAL_FEATURES, verbose=False)
    final_model.save_model(str(output_model))

    metrics_path = resolve_path(config["app"]["metrics_path"])
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # Keep a PII-minimised holdout replay file for the dashboard demo.
    validation_csv = resolve_path(config["app"]["validation_predictions_path"])
    validation_csv.parent.mkdir(parents=True, exist_ok=True)
    safe_columns = [
        "risk_score", "bucket", "order_value", "city", "state", "courier", "utm_source",
        "target_rto", "rto_cost_avoided_inr", "genuine_margin_lost_inr",
        "prepaid_discount_cost_inr", "net_impact_inr",
    ]
    test[safe_columns].to_csv(validation_csv, index=False)

    print(json.dumps({
        "model": str(output_model),
        "metrics": str(metrics_path),
        "validation_demo_data": str(validation_csv),
        "temporal_validation": metrics["temporal_validation"],
        "buckets": metrics["holdout_bucket_summary"],
        "scenario_impact": metrics["scenario_impact"],
    }, indent=2))


if __name__ == "__main__":
    main()
