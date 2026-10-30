# RTO Guard — Architecture and implementation notes

## 1. System overview

RTO Guard is a local-first decision-support prototype for reviewing COD orders before dispatch. It combines a tabular RTO-risk model, deterministic policy rules, an optional LLM address-quality check, a WhatsApp conversation simulator, and a small SQLite-backed founder dashboard.

```text
                 Historical COD orders (CSV)
                            |
                            v
                Data audit + normalisation
      (deduplicate derived frame, standardise status,
          exclude unresolved outcomes from labels)
                            |
                            v
                 Feature engineering
      (tabular signals + address-quality flags;
       excludes raw name/phone/address/pincode)
                            |
                            v
       Chronological training / validation pipeline
      CatBoost | Aug iteration selection | Sep holdout
                            |
                            v
           Saved CatBoost inference model + metadata

 New COD order -------------------------------+
       |                                       |
       v                                       v
 Deterministic feature builder          Optional LLM review
       |                                 (consented Hinglish /
       v                                  address usability)
 CatBoost RTO probability <------------------+
       |
       v
 Configurable policy thresholds (YAML / UI)
       |
       +----> Ship as-is
       +----> Ship after WhatsApp confirmation
       +----> Nudge to prepaid
       +----> Hold for human review
       |
       v
 Plain-English reason + scenario economics
       |
       +----> SQLite decision log ----> Founder dashboard
       |
       +----> WhatsApp mock state machine
```

## 2. Historical data pipeline

`scripts/train_model.py` reads the supplied CSV and builds a derived working DataFrame. It does not write to or replace the input CSV. The analytical steps are:

1. Read the source rows.
2. Remove exact duplicate rows in memory only.
3. Trim and uppercase status values so spelling/case variations map to the same final labels.
4. Exclude the single `IN_TRANSIT` record because it has no final delivery outcome.
5. Keep the remaining delivered/RTO outcomes for descriptive analysis and chronological model evaluation.
6. Treat zero/negative basket values as missing model inputs, not as genuine low-value baskets.
7. Record data-quality warnings rather than silently editing inconsistent customer-history values.

The raw file contains customer-like names, phone numbers and street addresses. It is therefore not included in the repository archive. The derived validation replay contains no order IDs, names, phone numbers, raw addresses or exact pincodes.

## 3. Feature engineering and privacy boundary

`rto_guard/features.py` defines one stable feature contract for both training and live inference.

- **Numeric features:** basket value, item count, customer prior-order / prior-RTO counters, a prior-RTO ratio, order hour/weekend, address length and simple address/name/phone/pincode/city quality flags.
- **Categorical features:** city, state, courier, acquisition source and a compact item-family token.
- **Excluded raw fields:** order ID, customer name, phone number, full free-text address and exact pincode are not passed to CatBoost.

The name/address fields are transformed into coarse flags before classification. An optional LLM can review the free-text name/address/city/state only when the operator has an API key and explicitly checks the consent control. That optional call is not needed to run the app. Its structured result may only prompt confirmation or human review; it cannot calculate RTO probability or cancel an order.

## 4. Model development and validation

The model is CatBoost because the problem is a small/medium tabular classification task with a mixture of numeric and categorical fields. A prompt-based LLM is not appropriate for calculating a calibrated order-level probability at scale.

The evaluation is chronological rather than random:

- April–July: iteration-selection training period.
- August: iteration-selection validation period.
- April–August: fit the validation model.
- September: untouched temporal holdout for the reported metrics and action replay.
- After the holdout is scored, the saved inference model is refit on all April–September labelled rows using the selected iteration count.

Latest reported holdout: 329 orders, 86 RTOs (26.14%); ROC-AUC 0.7492, average precision 0.5674 and Brier score 0.1630. These metrics indicate useful separation but not production-grade certainty. The three-order highest-risk bucket is too small to support a robust cancellation conclusion.

## 5. Decision policy and unit economics

`rto_guard/decisions.py` obtains a model score and maps it to four actions using editable thresholds in `config.yaml` (also editable in the Policy Settings tab):

| Predicted RTO score | Action |
|---|---|
| Below 20% | Ship as-is |
| 20% to below 30% | Ship after WhatsApp confirmation |
| 30% to below 70% | Offer ₹25 prepaid discount |
| 70% or above | Hold for manual review |

Automatic cancellation is disabled. First-time-customer protection is on and geography-only cancellation is off. The high-risk bucket is a queue for an operations person, not an instruction to cancel.

`rto_guard/economics.py` handles all arithmetic deterministically using the assignment assumptions:

- Expected RTO cost: ₹65 + ₹65 + ₹25 + (8% × ₹180) = ₹169.40.
- Delivered gross contribution: ₹649 × 62% = ₹402.38.
- Expected RTO-cost benefit from moving 28% COD RTO to 4% prepaid RTO: 24% × ₹169.40 = ₹40.66 per converted order.
- Less ₹25 discount: ₹15.66 before payment fees.
- Under the confirmation prevention / genuine-timeout assumptions in config, the confirmation decision crosses break-even at approximately 19.2%; the ship/confirm boundary is set at 20%.

Dashboard savings are scenario estimates under editable assumptions, not measured or causal effects.

## 6. WhatsApp simulator

`rto_guard/messaging.py` implements a deterministic state machine for both requested journeys.

- **Ship after confirmation:** initial confirmation message in English and Hinglish; YES confirms COD dispatch; NO records customer cancellation; unexpected reply prompts for YES/NO and then escalates to an agent; no reply by 24 hours means do not dispatch and mark cancelled, with an option to reorder.
- **Prepaid nudge:** initial ₹25 offer in English and Hinglish; YES generates a mock checkout link but does not mark the order paid; a separate `PAID` event confirms prepaid fulfilment; NO preserves COD and asks the customer to confirm receipt; unexpected replies are clarified and can escalate; an ignored offer expires after the configured 24-hour validity but does not cancel the COD order.
- Confirmation reminder is configured at 2 hours; prepaid reminder at 6 hours.

The simulator has manual buttons to advance to reminder/timeout states. It does not run a background scheduler or send real WhatsApp messages; this is intentionally a working mock as permitted by the assignment.

## 7. Dashboard and persistence

`app.py` provides five Streamlit tabs: Founder dashboard, Review an order, WhatsApp simulator, Data & model, and Policy settings.

`rto_guard/storage.py` stores decision events in SQLite. The founder view filters the previous calendar day using `Asia/Kolkata`, counts the four decision buckets, and aggregates the scenario estimates for avoided RTO cost, genuine contribution at risk, prepaid discount and net impact. A one-click September replay seeds demo-labelled rows so the dashboard is presentable before Shopify is connected. It is explicitly marked as demo data, not actual orders from the previous day.

For a hosted trial, the repository contains a Dockerfile and Railway configuration. SQLite requires a persistent volume. No Shopify, real WhatsApp provider, payment gateway, authentication system or cloud deployment is connected in this take-home version.

## 8. Key limitations and next validation work

- Only 1,987 labelled, deduplicated rows remain, with one six-month period from one sample brand.
- Four rows report two prior RTOs despite one prior order. They are preserved and flagged; confirm whether the field includes the current order before production, since history leakage would inflate model performance.
- City, state, courier and acquisition source may be proxies for logistics mix and require ongoing false-positive/calibration monitoring. No pincode blacklist is implemented.
- LLM integration is optional and has not been live-tested without an API key. Human review remains the fallback.
- Dashboard economics require a randomized/controlled operational test to validate response, conversion, time-out and cancellation assumptions.
- Before production: connect Shopify events; connect WhatsApp Business sandbox; run randomized holdout tests; check calibration and genuine-customer loss by cohort; set authentication, retention, access controls and monitoring; keep auto-cancel disabled until evidence supports a change.
