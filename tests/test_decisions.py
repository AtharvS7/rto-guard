import pandas as pd

from rto_guard.decisions import bucket_for_risk
from rto_guard.economics import delivered_gross_contribution, prepaid_discount_math, rto_cost_per_order
from rto_guard.features import engineer_features
from rto_guard.messaging import initial_message, process_reply, reminder_message, timeout_result


def base_config():
    return {
        "risk_thresholds": {
            "ship_as_is_below": 0.20,
            "confirm_below": 0.30,
            "nudge_below": 0.70,
        },
        "unit_economics": {
            "forward_shipping_inr": 65,
            "return_shipping_inr": 65,
            "packaging_handling_inr": 25,
            "damaged_return_rate": 0.08,
            "product_cost_inr": 180,
            "delivered_gross_margin": 0.62,
            "assignment_aov_inr": 649,
            "prepaid_rto_rate": 0.04,
            "prepaid_discount_inr": 25,
            "prepaid_conversion_assumption": 0.30,
            "confirmation_rto_prevention_assumption": 0.50,
            "confirmation_genuine_timeout_assumption": 0.05,
        },
    }


def test_bucket_boundaries_are_inclusive_on_upper_band():
    thresholds = base_config()["risk_thresholds"]
    assert bucket_for_risk(0.1999, thresholds) == "Ship as-is"
    assert bucket_for_risk(0.20, thresholds) == "Ship after confirmation"
    assert bucket_for_risk(0.30, thresholds) == "Nudge to prepaid"
    assert bucket_for_risk(0.70, thresholds) == "Hold / cancel"


def test_assignment_unit_economics():
    econ = base_config()["unit_economics"]
    assert round(rto_cost_per_order(econ), 2) == 169.40
    assert round(delivered_gross_contribution(econ), 2) == 402.38
    math = prepaid_discount_math(econ)
    assert round(math["expected_cost_saved_per_prepaid_conversion"], 2) == 40.66
    assert round(math["net_before_gateway_fee"], 2) == 15.66


def test_feature_engineering_does_not_expose_raw_name_or_address():
    order = pd.DataFrame([{
        "order_datetime": "2026-10-30 12:00:00", "customer_name": "Aarav Sharma",
        "phone": "9876543210", "address": "Station chowk se left, mandir ke saamne",
        "city": "Pune", "state": "Maharashtra", "pincode": 411001,
        "order_value": 649, "items": "Satin Bonnet", "num_items": 1,
        "customer_prior_orders": 0, "customer_prior_rto": 0,
        "utm_source": "meta_ads", "courier": "Delhivery",
    }])
    features = engineer_features(order)
    assert "customer_name" not in features.columns
    assert "address" not in features.columns
    assert features.iloc[0]["address_has_landmark"] == 1
    assert features.iloc[0]["has_customer_history"] == 0


def test_short_valid_name_is_not_flagged_as_suspicious():
    from rto_guard.features import engineer_features
    row = {
        "order_datetime": "2026-10-30 12:00:00", "customer_name": "Om",
        "phone": "9876543210", "address": "Flat 12, Lake View Society",
        "city": "Pune", "state": "Maharashtra", "pincode": 411001,
        "order_value": 649, "items": "Satin Bonnet", "num_items": 1,
        "customer_prior_orders": 0, "customer_prior_rto": 0,
        "utm_source": "direct", "courier": "Delhivery",
    }
    features = engineer_features(pd.DataFrame([row]))
    assert features.iloc[0]["name_is_suspicious"] == 0

    row["customer_name"] = "Test User"
    suspicious_features = engineer_features(pd.DataFrame([row]))
    assert suspicious_features.iloc[0]["name_is_suspicious"] == 1


def test_confirmation_yes_no_random_and_timeout():
    assert process_reply("Ship after confirmation", "YES")["status"] == "confirmed_to_ship"
    assert process_reply("Ship after confirmation", "NO")["status"] == "customer_cancelled"
    assert process_reply("Ship after confirmation", "maybe")["status"] == "needs_clarification"
    assert process_reply("Ship after confirmation", "still not sure", status="needs_clarification")["status"] == "manual_agent_review"
    assert timeout_result("Ship after confirmation")["status"] == "cancelled_no_confirmation"


def test_prepaid_flow_does_not_treat_yes_as_payment():
    yes = process_reply("Nudge to prepaid", "YES")
    assert yes["status"] == "payment_link_sent"
    paid = process_reply("Nudge to prepaid", "PAID", status="payment_link_sent")
    assert paid["status"] == "prepaid_payment_confirmed"
    no = process_reply("Nudge to prepaid", "NO")
    assert no["status"] == "cod_confirmation_required"
    cod_question = process_reply("Nudge to prepaid", "maybe", status="cod_confirmation_required")
    assert cod_question["status"] == "cod_confirmation_needs_clarification"
    cod_yes = process_reply("Nudge to prepaid", "YES", status="cod_confirmation_needs_clarification")
    assert cod_yes["status"] == "confirmed_to_ship_cod"
    assert process_reply("Nudge to prepaid", "maybe", status="cod_confirmation_needs_clarification")["status"] == "manual_agent_review"
    assert process_reply("Nudge to prepaid", "YES", status="offer_expired_cod_reconfirmation")["status"] == "confirmed_to_ship_cod"
    assert timeout_result("Nudge to prepaid")["status"] == "offer_expired_cod_reconfirmation"


def test_high_risk_is_held_and_not_auto_cancelled_by_default():
    from rto_guard.config import load_config
    from rto_guard.decisions import make_decision

    class FixedRiskModel:
        def predict_proba(self, features):
            return [[0.10, 0.90]]

    order = {
        "order_id": "TEST-900", "order_datetime": "2026-10-30 12:00:00",
        "customer_name": "Aarav Sharma", "phone": "9876543210",
        "address": "Flat 302, Lake View Society", "city": "Pune", "state": "Maharashtra",
        "pincode": 411001, "order_value": 649, "items": "Satin Bonnet", "num_items": 1,
        "customer_prior_orders": 0, "customer_prior_rto": 0,
        "utm_source": "meta_ads", "courier": "Delhivery", "payment_method": "COD",
    }
    result = make_decision(order, FixedRiskModel(), load_config())
    assert result["bucket"] == "Hold / cancel"
    assert result["operational_action"] == "Hold for manual review"
    assert result["auto_cancelled"] is False
    assert result["first_time_customer"] is True


def test_whatsapp_timing_and_reminders_use_configured_values():
    order = {"customer_name": "Aarav", "order_id": "A-1", "order_value": 649, "discount": 35}
    prepaid = initial_message("Nudge to prepaid", order, validity_hours=18)
    assert "Rs 35 off" in prepaid["english"]
    assert "18 hours" in prepaid["english"]
    assert "18 ghante" in prepaid["hinglish"]
    reminder = reminder_message("Nudge to prepaid", "A-1", discount=35, validity_hours=18)
    assert "Rs 35" in reminder
    assert "18 hours after the original offer" in reminder
    assert "original offer bhejne ke 18 ghante" in reminder
    confirmation = reminder_message("Ship after confirmation", "A-1")
    assert "YES or NO" in confirmation
    assert "YES ya" in confirmation
    assert "within 12 hours" in timeout_result("Ship after confirmation", 12)["message"]
    assert "after 18 hours" in timeout_result("Nudge to prepaid", 18)["message"]
