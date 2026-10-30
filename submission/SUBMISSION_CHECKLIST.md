# Assessment submission checklist

1. **GitHub repository:** publish this folder using the commands in the root README. Keep the raw customer CSV and `.env` out of Git.
2. **Business memo:** submit `founder_memo.pdf`; retain `founder_memo.docx` for editing.
3. **Decision log:** submit `decision_log.md`.
4. **AI usage log:** submit `ai_usage_log.md`.
5. **Supporting analysis:** include `data_findings.md` so the reported metrics and assumptions can be audited.
6. **Runnable demo:** install dependencies and run `streamlit run app.py`; load the explicitly labelled 329-row demo snapshot on the Founder dashboard.
7. **Tests:** run `pytest -q` from the project root.

The WhatsApp experience is intentionally a simulator. No real WhatsApp message, payment, Shopify fulfilment action or customer cancellation is triggered by this prototype.
