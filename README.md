# RTO Guard - COD Order Decision Desk

A runnable take-home prototype for a D2C haircare brand that wants to reduce Return-to-Origin (RTO) losses without blindly cancelling genuine first-time COD customers.

## What is included

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the component diagram, data flow, privacy boundary, model-validation design, decision policy, WhatsApp state machine and deployment limitations.


- **Part 1 - Decision engine:** a CatBoost classifier trained on the supplied COD outcomes, deterministic feature engineering, four configurable action buckets, plain-English explanations, and manual-review safeguards.
- **Part 2 - WhatsApp mock:** English + Hinglish confirmation and prepaid nudges, simulated YES / NO / unexpected replies, reminders, 24-hour timeouts, and a mock checkout link. It does not send real messages or collect payments.
- **Part 3 - Founder dashboard:** yesterday's bucket counts, estimated avoided RTO cost, genuine contribution at risk, prepaid discounts, and net impact. A 329-order demo replay is available so the dashboard can be shown immediately.
- **Part 4 - Submission memo:** founder memo PDF/DOCX, decision log and AI-use log are in `submission/`.
- **Tests:** boundary, economics, feature-privacy, and WhatsApp flow tests are in `tests/`.

## Run locally in under 10 minutes

Requires Python 3.10 or 3.11.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate

# 2. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Launch the app (the trained model is already included)
streamlit run app.py
```

Open the local Streamlit URL printed in the terminal. Start in **Founder dashboard** and click **Load demo snapshot (329 validation orders)** to populate yesterday's demo view. The replay is explicitly labelled demo data; it is not a claim that these orders arrived yesterday.

### Optional: retrain on the supplied CSV

The raw CSV is deliberately not committed because it contains customer names, phone numbers and delivery addresses. Put the assessment CSV at `data/orders_sample.csv` locally, then run:

```bash
python scripts/train_model.py --input data/orders_sample.csv
```

The training script removes exact duplicates, normalizes status labels, excludes non-final statuses, treats zero/negative basket values as missing model inputs, selects model iterations using August, then runs an April-August to September temporal validation, saves a model to `artifacts/rto_model.cbm`, and writes PII-minimised validation outputs.

### Optional: AI address-quality review

The classifier does not need an LLM to calculate risk or economics. An opt-in LLM module is provided for one task where language understanding can help: judging whether English/Hinglish free-text delivery details are actionable. It must not estimate RTO probability or cancel an order.

```bash
cp .env.example .env
# Add your API key and, if needed, an available model name.
```

The order form asks for explicit consent before the customer's name and address are sent to the configured API. The app never stores the raw name, phone or address in its decision database. Without an API key or consent, the deterministic fallback runs.

## Decision policy

Default thresholds live in `config.yaml` and can also be changed from the **Policy settings** tab:

| Model RTO score | Action |
|---|---|
| `< 20%` | Ship as-is |
| `20% to < 30%` | Ship after WhatsApp confirmation |
| `30% to < 70%` | Nudge to prepaid (₹25 offer) |
| `>= 70%` | Hold / cancel bucket; manual review by default |

Automatic cancellation is **disabled**. The first-time-buyer protection is enabled, and geography-only cancellation is disabled. A high-risk order is held for an operations decision; it is not silently cancelled by the model.

## Economics and assumptions

The supplied brief's assumptions are used for business calculations, not the sample's higher observed AOV:

- Expected RTO cost = ₹65 forward + ₹65 return + ₹25 packaging/handling + (8% damaged × ₹180 product cost) = **₹169.40 per RTO**.
- Gross contribution on a delivered order = ₹649 × 62% = **₹402.38** before CAC.
- A ₹25 prepaid discount is 3.85% of the ₹649 assignment AOV. Moving an order from 28% COD RTO to 4% prepaid RTO saves an expected ₹40.66 in RTO costs per prepaid conversion; after the discount, the expected gain is ₹15.66 before payment fees.
- The scenario assumes 30% prepaid conversion, 50% prevention of would-be RTOs after confirmation, and 5% genuine-order loss from confirmation timeout. These are explicit, editable assumptions, not measured treatment effects.

The dashboard's net impact is a scenario versus shipping all COD orders unchanged. It is not audited cash savings or a causal A/B-test result. The CAC is treated as already spent at decision time and is not subtracted a second time from the incremental gross-contribution loss.

## Model validation and limitations

- Data audit: 2,018 raw rows; 30 exact duplicates removed in a derived analysis frame; 1 non-final `IN_TRANSIT` row excluded; 1,987 final-labelled records retained. The uploaded CSV itself is never overwritten.
- Four labelled rows have inconsistent history counters (`customer_prior_rto` = 2 while `customer_prior_orders` = 1). They remain in the source and training frame as supplied; verify whether the history counter includes the current order before using it operationally.
- Temporal validation: train on April-August 2026 (1,658 orders), test on September 2026 (329 orders).
- Validation: ROC-AUC, PR-AUC and Brier score are recorded in `artifacts/model_metadata.json` and shown in the app.
- September action replay: the saved CSV contains only scores, coarse segments, order values and outcomes needed for retrospective validation; it excludes order IDs, names, phones, raw addresses and exact pincodes.

This is a small, single-brand dataset. City/state and acquisition-source relationships may reflect courier mix or acquisition mix, not customer intent. There is no pincode blocklist. Monitor calibration and false-positive rates by region, new/repeat customer status and acquisition channel before a production rollout. The 3-order highest-risk hold bucket in the holdout is too small to justify automatic cancellation.

## WhatsApp flow rules

| Event | Confirmation flow | Prepaid-nudge flow |
|---|---|---|
| Immediate | Ask customer to confirm they can receive COD | Offer ₹25 prepaid discount; YES requests checkout link, NO keeps COD |
| YES | Confirm and ship | Send mock payment link; switch to prepaid only after a `PAID`/payment confirmation |
| NO | Customer-requested cancellation | Keep COD and ask for a separate delivery confirmation |
| Unexpected reply | Ask once for YES or NO; then agent review | Clarify YES for payment link / NO for COD |
| Reminder | One reminder after 2 hours | One reminder after 6 hours |
| No response in 24 hours | Do not dispatch; mark cancelled and allow reorder | Expire discount; keep original COD order and send confirmation / agent follow-up, do not cancel just because an offer was ignored |

## Railway deployment

The repository includes a `Dockerfile`, `railway.json` and `Procfile` for Railway's Docker deployment. After pushing the folder to a GitHub repository:

1. Create a new Railway project and deploy from that GitHub repository.
2. Add a Railway persistent volume mounted at `/app/data` (SQLite is otherwise ephemeral across redeploys).
3. Set `RTO_DB_PATH=/app/data/rto_guard.db` in Railway variables.
4. Add `OPENAI_API_KEY` only if the optional AI address review is required. Never put secrets in source control.
5. Railway supplies `PORT`; the Docker command binds Streamlit to `0.0.0.0:$PORT` and the configured health check is `/_stcore/health`.

Shopify, a real WhatsApp Business provider, payment gateway and production authentication are intentionally not connected in this take-home mock. They are next-step integrations, not prerequisites to run the assessment.

## Tests

```bash
pytest -q
```

## Privacy note

Do not commit the original assessment CSV or a real `.env` file. The supplied CSV includes customer-like PII. The model uses limited derived flags; the decision log stores order reference, score, bucket, coarse city/state and impact values, not the customer's name, phone or street address. Production use needs an approved retention policy, access controls and a privacy review.

## Publish to GitHub

The GitHub connector in this workspace can inspect and update an existing repository, but it cannot create a new repository. Create an empty repository in your GitHub account (for example, `rto-guard`; do not initialize it with a README), then run these commands from this folder:

```bash
git init
git add .
git commit -m "Complete RTO Guard COD decision prototype"
git branch -M main
git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/rto-guard.git
git push -u origin main
```

Before pushing, check `git status` and confirm that `.env`, the raw assessment CSV, local SQLite files and caches are not staged. The `.gitignore` and `.dockerignore` files exclude these items by default. The PII-minimised September replay and trained model are included so the app starts without the raw dataset.

## Assessment submission checklist

- `submission/founder_memo.pdf` — founder-facing memo (2 pages).
- `submission/founder_memo.docx` — editable memo source.
- `submission/decision_log.md` — important decisions and alternatives.
- `submission/ai_usage_log.md` — tools used, corrections and limitations.
- `submission/data_findings.md` — audit, descriptive findings, holdout performance and scenario maths.
- `tests/` — automated checks for policy boundaries, economics, feature privacy and message states.
