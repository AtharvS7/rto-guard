# AI usage log

## Tools used

- **ChatGPT (GPT-6):** interpreted the take-home brief; designed the decision policy and four workstreams; inspected the supplied CSV; drafted the implementation, tests, README, founder memo, decision log and this usage log; reviewed model metrics and business assumptions.
- **OpenAI API integration (optional in the app):** a small Structured Outputs call can be enabled by the operator to review English/Hinglish address text and return a typed quality assessment. It is not called by default and is not required to run the project. The operator must set an API key and explicitly consent in the form before sending name/address data.
- **CatBoost / pandas / scikit-learn:** trained and evaluated the tabular model and computed the metrics. These are deterministic code libraries, not LLMs.

## Examples of weak or incorrect AI/code output caught and corrected

1. **Pandas `items` attribute collision.** An exploratory feature-engineering draft used `row.items` as though it were the CSV column; Pandas resolves that name to the DataFrame/Series method. The resulting `AttributeError` was caught immediately and changed to explicit bracket indexing (`row["items"]`).
2. **Misleading class-weighted risk scores.** An early CatBoost experiment used automatic class balancing. It improved classification emphasis but pushed score levels upward and worsened the Brier score (about 0.20-0.22 in those trials). It was removed; the final unweighted temporal validation model had a Brier score 0.1630 and ROC-AUC 0.7492 after the short-name false-positive rule was tightened. The scores are still treated as estimates, not certainty.
3. **Prepaid `YES` is not proof of payment.** The initial conversational flow risked treating a simple YES as a completed prepaid order. The simulator was tightened so YES only requests a checkout link; it switches to prepaid fulfilment only after a separate `PAID` / payment-confirmation event.

## Human checks and limitations

- Verified economics manually: expected RTO cost = ₹169.40; delivered gross contribution = ₹402.38; prepaid savings at the brief's baseline = ₹40.66; net after ₹25 discount = ₹15.66 per prepaid conversion before gateway fees.
- Reviewed the temporal validation split and the small sample size of the highest-risk bucket. Three hold-bucket examples are insufficient to justify automatic cancellation.
- A source audit also found four rows where `customer_prior_rto` is 2 while `customer_prior_orders` is 1. The raw data was not changed; the anomaly is disclosed in the findings and must be resolved with the source owner before production because it could contaminate a history feature.
- Confirmed that the original raw CSV is not copied into the repository or demo replay. The model does not ingest raw names, phones, street addresses or exact pincodes.
- No claim is made that AI generated causal savings or that the optional address model has been experimentally validated. The optional OpenAI call was not live-tested in this audit because no API key was configured. The dashboard uses editable scenario assumptions.
