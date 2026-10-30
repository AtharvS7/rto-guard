# Data findings used in the founder memo

## Data audit

| Check | Result |
|---|---:|
| Raw CSV rows | 2,018 |
| Exact duplicate rows removed | 30 |
| Remaining rows before status filter | 1,988 |
| Non-final `IN_TRANSIT` rows excluded | 1 |
| Final labelled orders | 1,987 |
| Delivered | 1,422 |
| RTO | 565 |
| Clean historical RTO rate | 28.43% |
| Dataset date span | 1 Apr - 30 Sep 2026 |
| Mean / median sample order value | ₹920 / ₹748 |
| Zero-value rows flagged; value treated as missing in model | 3 |
| Inconsistent prior-history rows flagged (2 prior RTOs, 1 prior order) | 4 |

Status values were normalized by trimming whitespace and uppercasing before the filter. Three zero-value records remain in the labelled-outcome audit, but order value is treated as missing for those rows in the model rather than as a genuine low basket value. Four records report two prior RTOs despite only one prior order. The source values were not edited or silently repaired; the four rows remain in the training frame. This is a data-quality warning and a potential leakage concern if the history field was calculated using the current order outcome. Confirm the source-system definition before production use.

## Segment findings

| Segment | Orders | RTOs | RTO rate |
|---|---:|---:|---:|
| Bihar | 145 | 78 | 53.8% |
| Uttar Pradesh | 195 | 86 | 44.1% |
| Maharashtra | 385 | 70 | 18.2% |
| Meta Ads | 765 | 239 | 31.2% |
| Direct | 253 | 58 | 22.9% |
| 0 prior RTOs | 1,845 | 501 | 27.2% |
| 1 prior RTO | 108 | 38 | 35.2% |
| 2 prior RTOs | 34 | 26 | 76.5% |

The 2-prior-RTO segment is only 34 examples. It supports a strong manual-review trigger but not an automatic ban. The state and channel figures are descriptive associations and do not establish causality.

## Model validation

- Train: 1,658 orders from April-August 2026.
- Temporal holdout: 329 orders from September 2026; observed RTO rate 26.14% (86 RTOs).
- CatBoost validation metrics: ROC-AUC 0.7492, average precision 0.5674, Brier score 0.1630. Iteration count was selected using April-July training and August validation, then the validation model was refit on all April-August orders before September was scored. The saved inference model was subsequently refit on all April-September labelled orders with that iteration count.
- September bucket assignment: 88 ship as-is (8/88 RTO; 9.1%), 125 confirmation (27/125; 21.6%), 113 prepaid nudge (48/113; 42.5%), and 3 hold / manual review (3/3; 100%). The hold bucket is only three observations and is not statistically reliable.

## Impact scenario (not causal savings)

| Component | September holdout estimate |
|---|---:|
| Expected RTO cost avoided | ₹5,104.81 |
| Gross contribution at risk from genuine cancellations | ₹2,260.80 |
| Prepaid discount cost | ₹847.50 |
| Net impact vs ship-as-is | +₹1,996.60 |

Assumptions are declared in `config.yaml`: 50% of would-be RTOs prevented by confirmation, 5% of genuine confirmation orders fail to confirm, 30% prepaid conversion, ₹25 discount and the assignment's 4% prepaid RTO rate. The three hold-bucket orders are treated as ultimately cancelled after human review for this scenario, although the operating policy is manual review and does not auto-cancel. A live randomized test is required to validate the impact.
