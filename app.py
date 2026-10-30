from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml
from dotenv import load_dotenv

from rto_guard.config import ROOT, load_config, resolve_path
from rto_guard.decisions import BUCKETS, make_decision
from rto_guard.economics import prepaid_discount_math, rto_cost_per_order
from rto_guard.llm_quality import is_llm_configured, review_address_with_llm
from rto_guard.messaging import initial_message, process_reply, reminder_message, timeout_result
from rto_guard.model import load_model
from rto_guard.storage import log_decision, read_decisions, seed_demo_snapshot

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="RTO Guard | COD Decision Desk", page_icon="📦", layout="wide")


@st.cache_resource
def get_model(model_path: str):
    return load_model(model_path)


def format_inr(value: float) -> str:
    return f"₹{value:,.0f}"


def get_timezone(config):
    return ZoneInfo(config["app"].get("timezone", "Asia/Kolkata"))


def local_now(config):
    return datetime.now(get_timezone(config))


def load_metrics(config):
    path = resolve_path(config["app"]["metrics_path"])
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_thresholds(config, ship_below: float, confirm_below: float, nudge_below: float):
    config["risk_thresholds"] = {
        "ship_as_is_below": float(ship_below),
        "confirm_below": float(confirm_below),
        "nudge_below": float(nudge_below),
    }
    config_path = ROOT / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    st.cache_resource.clear()


def show_header():
    st.title("RTO Guard")
    st.caption("COD order decision desk · Model-assisted operations · India")


def dashboard(config):
    st.subheader("Founder dashboard")
    st.caption("Yesterday's logged decisions and scenario-based financial impact. Demo replays are clearly labelled.")
    db_path = resolve_path(config["app"]["database_path"])
    all_rows = read_decisions(db_path)
    now = local_now(config)
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    if not all_rows.empty:
        all_rows["created_local"] = pd.to_datetime(all_rows["created_at"], errors="coerce", utc=True).dt.tz_convert(get_timezone(config))
        daily = all_rows[all_rows["created_local"].dt.date.astype(str) == yesterday].copy()
    else:
        daily = pd.DataFrame()

    if daily.empty:
        st.info(f"No order decisions have been logged for {yesterday} yet.")
        demo_path = resolve_path(config["app"]["validation_predictions_path"])
        if demo_path.exists():
            st.warning("Load the September holdout replay to preview the dashboard. It will be inserted as DEMO data dated yesterday, not presented as live orders.")
            if st.button("Load demo snapshot (329 validation orders)", type="primary"):
                created_at = datetime.combine(now.date() - timedelta(days=1), datetime.min.time(), tzinfo=get_timezone(config)).isoformat()
                count = seed_demo_snapshot(db_path, demo_path, created_at)
                st.success(f"Loaded {count} demo decisions. Refreshing the dashboard.")
                st.rerun()
        return

    if (daily["mode"] == "demo").any():
        st.warning("This view contains a demo replay of September validation orders. Replace it with live decisions when integrating Shopify.")

    bucket_counts = daily["bucket"].value_counts().reindex(BUCKETS, fill_value=0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Orders reviewed yesterday", f"{len(daily):,}")
    c2.metric("RTO cost avoided (est.)", format_inr(daily["rto_cost_avoided_inr"].sum()))
    c3.metric("Genuine margin lost (est.)", format_inr(daily["genuine_margin_lost_inr"].sum()))
    c4.metric("Net impact (est.)", format_inr(daily["net_impact_inr"].sum()))

    st.markdown("**Orders by decision bucket**")
    count_cols = st.columns(4)
    for col, bucket in zip(count_cols, BUCKETS):
        col.metric(bucket, f"{int(bucket_counts[bucket]):,}")

    a, b = st.columns([1.05, 1])
    with a:
        counts_frame = bucket_counts.rename_axis("Decision").reset_index(name="Orders")
        fig = px.bar(counts_frame, x="Decision", y="Orders", color="Decision", text="Orders")
        fig.update_layout(showlegend=False, margin=dict(l=8, r=8, t=10, b=8), height=320)
        st.plotly_chart(fig, use_container_width=True)
    with b:
        impacts = pd.DataFrame({
            "Component": ["Avoided RTO cost", "Lost genuine margin", "Prepaid discounts"],
            "INR": [
                daily["rto_cost_avoided_inr"].sum(),
                -daily["genuine_margin_lost_inr"].sum(),
                -daily["prepaid_discount_cost_inr"].sum(),
            ],
        })
        fig = px.bar(impacts, x="Component", y="INR", text_auto=".0f")
        fig.update_layout(margin=dict(l=8, r=8, t=10, b=8), height=320, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Decision log**")
    display_cols = ["created_at", "order_id", "mode", "bucket", "risk_score", "order_value", "city", "operational_action"]
    st.dataframe(daily[display_cols].sort_values("created_at", ascending=False), use_container_width=True, hide_index=True)
    st.caption("Impact is an expected-value scenario, not audited savings. It uses the response, conversion and cancellation assumptions in config.yaml.")


def new_order(config, model):
    st.subheader("Review a new COD order")
    st.caption("Enter the order details to get one of the four actions and an operations-ready explanation.")
    with st.form("new_order_form"):
        left, right = st.columns(2)
        with left:
            order_id = st.text_input("Order ID", value=f"DEMO-{local_now(config):%H%M%S}")
            customer_name = st.text_input("Customer name", value="Aarav Sharma")
            phone = st.text_input("Phone number", value="9876543210", help="Used only for a local validity flag; not sent to the model.")
            address = st.text_area("Delivery address", value="Flat 302, Lake View Society, near Hanuman Mandir")
            city = st.text_input("City", value="Pune")
            state = st.selectbox("State", ["Maharashtra", "Delhi", "Karnataka", "Uttar Pradesh", "Bihar", "Telangana", "Tamil Nadu", "Rajasthan", "West Bengal", "Madhya Pradesh", "Assam", "Other"])
        with right:
            pincode = st.text_input("Pincode", value="411001", max_chars=6)
            order_value = st.number_input("Order value (₹)", min_value=1, max_value=100000, value=649, step=50)
            items = st.selectbox("Product group", ["Satin Bonnet", "Satin Pillowcase", "Hair Oil 100ml", "Haircare bundle", "Accessories bundle", "Heatless Curling Set", "Other"])
            num_items = st.number_input("Number of items", min_value=1, max_value=20, value=1)
            customer_prior_orders = st.number_input("Previous delivered / placed orders", min_value=0, max_value=100, value=0)
            customer_prior_rto = st.number_input("Previous RTOs", min_value=0, max_value=100, value=0)
            utm_source = st.selectbox("Acquisition source", ["meta_ads", "google_ads", "direct", "instagram_organic", "influencer", "whatsapp_broadcast"])
            courier = st.selectbox("Proposed courier", ["Delhivery", "Shadowfax", "Xpressbees", "Ecom Express", "BlueDart"])
        st.divider()
        use_llm = st.checkbox(
            "Use optional AI address review (sends customer name + address to the configured OpenAI API)",
            value=False,
            disabled=not is_llm_configured(),
        )
        if not is_llm_configured():
            st.caption("Optional AI review is unavailable until OPENAI_API_KEY is configured in your local .env file.")
        consent = st.checkbox("I have permission to process these customer details for this demo.", value=False)
        submitted = st.form_submit_button("Evaluate COD order", type="primary", use_container_width=True)

    if submitted:
        if not consent:
            st.error("Please confirm you have permission to process the customer details.")
            return
        if int(customer_prior_rto) > int(customer_prior_orders):
            st.error("Previous RTO count cannot exceed the previous order count.")
            return
        pincode_clean = "".join(ch for ch in pincode if ch.isdigit())
        order = {
            "order_id": order_id.strip() or "ORDER-UNKNOWN",
            "order_datetime": local_now(config).strftime("%Y-%m-%d %H:%M:%S"),
            "customer_name": customer_name,
            "phone": phone,
            "address": address,
            "city": city,
            "state": state,
            "pincode": float(pincode_clean) if len(pincode_clean) == 6 else None,
            "order_value": float(order_value),
            "items": items,
            "num_items": int(num_items),
            "customer_prior_orders": int(customer_prior_orders),
            "customer_prior_rto": int(customer_prior_rto),
            "utm_source": utm_source,
            "courier": courier,
            "payment_method": "COD",
        }
        llm_result = None
        if use_llm:
            try:
                with st.spinner("Reviewing address wording..."):
                    llm_result = review_address_with_llm(customer_name, address, city, state)
            except Exception as exc:
                st.warning(f"AI address review failed safely; continuing with the deterministic model. Details: {exc}")
        result = make_decision(order, model, config, llm_result)
        log_decision(
            resolve_path(config["app"]["database_path"]), order, result,
            local_now(config).isoformat(), mode="live",
        )
        st.session_state["last_order_result"] = result
        st.session_state["last_order_details"] = {k: v for k, v in order.items() if k not in {"phone", "address", "customer_name"}}

    if "last_order_result" in st.session_state:
        result = st.session_state["last_order_result"]
        details = st.session_state.get("last_order_details", {})
        st.divider()
        st.markdown("### Decision result")
        a, b, c = st.columns(3)
        a.metric("Recommended bucket", result["bucket"])
        b.metric("Estimated RTO risk", f"{result['risk_percent']:.1f}%")
        c.metric("Operational action", result["operational_action"])
        st.info(result["reason"])
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Avoided RTO cost (est.)", format_inr(result["rto_cost_avoided_inr"]))
        e2.metric("Genuine margin at risk", format_inr(result["genuine_margin_lost_inr"]))
        e3.metric("Discount cost (est.)", format_inr(result["prepaid_discount_cost_inr"]))
        e4.metric("Net impact (est.)", format_inr(result["net_impact_inr"]))
        if result["bucket"] in {"Ship after confirmation", "Nudge to prepaid"}:
            st.success("Open the WhatsApp Simulator tab to preview the customer message and reply handling.")
        if result.get("llm_assessment"):
            with st.expander("Optional AI address assessment"):
                st.json(result["llm_assessment"])
        st.caption("Order details saved to the local decision log. The app does not persist full address, phone number or customer name.")


def whatsapp_simulator(config):
    st.subheader("WhatsApp confirmation simulator")
    st.caption("A working mock only. No real WhatsApp message is sent and no payment is collected.")
    action = st.selectbox("Flow", ["Ship after confirmation", "Nudge to prepaid"], key="wa_action")
    c1, c2, c3 = st.columns(3)
    with c1:
        name = st.text_input("Customer first name", value="Aarav", key="wa_name")
    with c2:
        order_id = st.text_input("Order ID", value="KT-DEMO-1001", key="wa_order")
    with c3:
        amount = st.number_input("Order amount (₹)", min_value=1, max_value=100000, value=649, key="wa_amount")

    if st.button("Start / reset conversation", type="primary"):
        messages = initial_message(
            action,
            {"customer_name": name, "order_id": order_id, "order_value": amount, "discount": config["unit_economics"]["prepaid_discount_inr"]},
            validity_hours=int(config["messaging"]["prepaid_discount_expiry_hours"]),
        )
        st.session_state["wa_messages"] = [
            {"speaker": "System", "text": "Mock WhatsApp session created."},
            {"speaker": "Business (English)", "text": messages["english"]},
            {"speaker": "Business (Hinglish)", "text": messages["hinglish"]},
        ]
        st.session_state["wa_status"] = "pending"
        st.session_state["wa_flow"] = action

    confirm_reminder_h = int(config["messaging"]["confirmation_reminder_after_hours"])
    prepaid_reminder_h = int(config["messaging"]["prepaid_reminder_after_hours"])
    response_deadline_h = int(config["messaging"]["response_deadline_hours"])
    prepaid_expiry_h = int(config["messaging"]["prepaid_discount_expiry_hours"])
    reminder_after_h = confirm_reminder_h if action == "Ship after confirmation" else prepaid_reminder_h
    timeout_after_h = response_deadline_h if action == "Ship after confirmation" else prepaid_expiry_h
    st.caption(
        f"Timing: send immediately; reminder after {reminder_after_h} hours; "
        f"{timeout_after_h}-hour deadline. Buttons below simulate these events; no background scheduler runs."
    )

    if "wa_messages" in st.session_state:
        if st.session_state.get("wa_flow") != action:
            st.warning("The selected flow differs from the active conversation. Start / reset to switch flows.")
        for msg in st.session_state["wa_messages"]:
            with st.chat_message("user" if msg["speaker"] == "Customer" else "assistant"):
                st.markdown(f"**{msg['speaker']}**")
                st.write(msg["text"])
        reply = st.text_input("Simulated customer reply", key="wa_reply")
        send_col, reminder_col, timeout_col = st.columns(3)
        with send_col:
            send = st.button("Send reply")
        with reminder_col:
            remind = st.button(f"Simulate {reminder_after_h}-hour reminder")
        with timeout_col:
            timeout = st.button(f"Simulate {timeout_after_h}-hour timeout")
        if send and reply.strip():
            current_action = st.session_state.get("wa_flow", action)
            current_status = st.session_state.get("wa_status", "pending")
            result = process_reply(current_action, reply, current_status)
            st.session_state["wa_messages"].append({"speaker": "Customer", "text": reply.strip()})
            st.session_state["wa_messages"].append({"speaker": "Business", "text": result["message"]})
            st.session_state["wa_status"] = result["status"]
            if result["status"] == "payment_link_sent":
                st.session_state["wa_messages"].append({"speaker": "Business", "text": "[MOCK] Checkout link: https://checkout.example.test/order/" + str(order_id) + " - pay to switch this order to prepaid."})
            st.rerun()
        if remind:
            current_action = st.session_state.get("wa_flow", action)
            current_discount = int(config["unit_economics"]["prepaid_discount_inr"])
            current_validity = int(config["messaging"]["prepaid_discount_expiry_hours"])
            st.session_state["wa_messages"].append({
                "speaker": "Business",
                "text": reminder_message(current_action, order_id, current_discount, current_validity),
            })
            st.rerun()
        if timeout:
            current_action = st.session_state.get("wa_flow", action)
            deadline = response_deadline_h if current_action == "Ship after confirmation" else prepaid_expiry_h
            result = timeout_result(current_action, deadline)
            st.session_state["wa_status"] = result["status"]
            st.session_state["wa_messages"].append({"speaker": f"System ({deadline}h)", "text": result["message"]})
            st.rerun()
        st.caption(f"Conversation state: {st.session_state.get('wa_status', 'pending')}")


def data_and_model(config, metrics):
    st.subheader("Data and model quality")
    if not metrics:
        st.error("Model metadata is missing. Run the training command in the README.")
        return
    audit = metrics["data_audit"]
    validation = metrics["temporal_validation"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clean labelled orders", f"{audit['clean_labeled_rows']:,}")
    c2.metric("Historical RTO rate", f"{audit['rto_rate']:.1%}")
    c3.metric("September holdout AUC", f"{validation['roc_auc']:.3f}")
    c4.metric("September holdout orders", f"{validation['holdout_orders']:,}")
    st.caption(
        f"Raw rows: {audit['raw_rows']:,} · exact duplicate rows removed: {audit['exact_duplicate_rows_removed']} · "
        f"unresolved statuses excluded: {audit['unresolved_status_rows_excluded']} · "
        f"inconsistent prior-history rows: {audit.get('inconsistent_prior_history_rows', 0)} · "
        f"date span: {audit['date_min']} to {audit['date_max']}"
    )
    st.markdown("**Temporal validation**")
    st.write(
        f"Trained on {validation['train_orders']:,} orders from {validation['train_period']}; "
        f"tested on {validation['holdout_orders']:,} later orders from {validation['holdout_period']}. "
        f"ROC-AUC: {validation['roc_auc']:.3f}; PR-AUC: {validation['average_precision']:.3f}; "
        f"Brier score: {validation['brier_score']:.3f}."
    )
    st.markdown("**September action replay (outcomes were known only for this retrospective validation)**")
    buckets = pd.DataFrame(metrics["holdout_bucket_summary"])
    buckets["Observed RTO rate"] = buckets["observed_rto_rate"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
    st.dataframe(buckets[["bucket", "orders", "observed_rto_orders", "Observed RTO rate", "mean_model_score"]], use_container_width=True, hide_index=True)
    st.markdown("**Where the historical data is concentrated**")
    segments = metrics["segments"]
    state_df = pd.DataFrame(segments["state"])
    state_df["rto_rate_pct"] = state_df["rto_rate"] * 100
    fig = px.bar(state_df, x="state", y="rto_rate_pct", hover_data=["orders", "rto_orders"], text=state_df["rto_rate_pct"].map(lambda v: f"{v:.0f}%"))
    fig.update_layout(height=330, xaxis_title="State", yaxis_title="Observed RTO rate (%)", margin=dict(l=8, r=8, t=10, b=8))
    st.plotly_chart(fig, use_container_width=True)
    top_features = pd.DataFrame(metrics["feature_importance"][:10])
    fig = px.bar(top_features.sort_values("importance"), x="importance", y="feature", orientation="h")
    fig.update_layout(height=350, xaxis_title="CatBoost feature importance", yaxis_title="", margin=dict(l=8, r=8, t=10, b=8))
    st.plotly_chart(fig, use_container_width=True)
    st.warning("Geography and acquisition source are associations, not proof of customer intent. No pincode blacklist is implemented. The validation set is one month and small; monitor drift before any scale-up.")


def policy_settings(config):
    st.subheader("Business-editable thresholds")
    st.caption("Change the thresholds without editing Python. Save updates config.yaml for subsequent decisions.")
    thresholds = config["risk_thresholds"]
    ship_below = st.slider("Ship as-is below", 0.0, 0.50, float(thresholds["ship_as_is_below"]), 0.01)
    confirm_below = st.slider("Confirmation below", 0.05, 0.80, float(thresholds["confirm_below"]), 0.01)
    nudge_below = st.slider("Prepaid nudge below", 0.10, 0.99, float(thresholds["nudge_below"]), 0.01)
    st.caption("Risk ≥ prepaid-nudge threshold enters Hold / cancel. Hold is still manual by default.")
    if not ship_below < confirm_below < nudge_below:
        st.error("Threshold order must be: Ship as-is < Confirmation < Prepaid nudge < Hold / cancel.")
    else:
        st.write(f"Current policy: <{ship_below:.0%} ship · {ship_below:.0%}-{confirm_below:.0%} confirm · {confirm_below:.0%}-{nudge_below:.0%} nudge · ≥{nudge_below:.0%} hold.")
        if st.button("Save threshold changes", type="primary"):
            save_thresholds(config, ship_below, confirm_below, nudge_below)
            st.success("Thresholds saved in config.yaml. Reload the page to use them in new decisions.")
    st.divider()
    st.markdown("**Financial assumptions**")
    economics = config["unit_economics"]
    discount = prepaid_discount_math(economics)
    a, b, c = st.columns(3)
    a.metric("Expected RTO cost", format_inr(rto_cost_per_order(economics)))
    b.metric("Gross contribution / delivered order", format_inr(economics["assignment_aov_inr"] * economics["delivered_gross_margin"]))
    c.metric("Prepaid net per conversion", format_inr(discount["net_before_gateway_fee"]))
    st.write(
        f"A ₹{economics['prepaid_discount_inr']:.0f} prepaid discount is {discount['discount_pct_of_assignment_aov']:.2f}% of the ₹{economics['assignment_aov_inr']:.0f} assignment AOV. "
        f"The COD-to-prepaid RTO difference (28% to {economics['prepaid_rto_rate']:.0%}) is worth about {format_inr(discount['expected_cost_saved_per_prepaid_conversion'])} in expected RTO cost; "
        f"after discount, the modelled gain is {format_inr(discount['net_before_gateway_fee'])} per converted order before payment fees."
    )
    st.caption("Automatic cancellation is disabled in config.yaml. First-time customers are protected from auto-cancellation and geography alone never triggers cancellation.")


def main():
    config = load_config()
    model_path = resolve_path(config["app"]["model_path"])
    if not model_path.exists():
        st.error("The trained model artifact is missing. Run `python scripts/train_model.py --input path/to/orders_sample.csv` from the project folder.")
        st.stop()
    model = get_model(str(model_path))
    metrics = load_metrics(config)
    show_header()
    with st.sidebar:
        st.markdown("### Runtime status")
        st.success("Risk model loaded")
        st.write("Optional AI address review: " + ("configured" if is_llm_configured() else "not configured"))
        st.write("WhatsApp: mock simulator")
        st.write("Shopify: not connected in this take-home prototype")
        st.divider()
        st.caption("The score is decision support. Review holds manually; do not deploy auto-cancellation without a controlled test.")

    tabs = st.tabs(["Founder dashboard", "Review an order", "WhatsApp simulator", "Data & model", "Policy settings"])
    with tabs[0]:
        dashboard(config)
    with tabs[1]:
        new_order(config, model)
    with tabs[2]:
        whatsapp_simulator(config)
    with tabs[3]:
        data_and_model(config, metrics)
    with tabs[4]:
        policy_settings(config)


if __name__ == "__main__":
    main()
