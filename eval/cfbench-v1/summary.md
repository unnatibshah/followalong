## CF-Bench results

| System | Acc. | Bal. acc. | Done recall | False advance | Mistakes flagged | $/check |
|---|---|---|---|---|---|---|
| Pixel similarity to end frame (LOTO threshold) | 120 of 211 (57%) | | | 64 of 123 | | $0 |
| Muse Spark only (whole frame, VLM-as-judge) | 72% | 70% | 61% | 26 of 123 (21%) | 97 of 123 | $0.0024 |
| SAM 3.1 measured checks only | 72% | 75% | 89% | 48 of 123 (39%) | 75 of 123 | $0.004 |
| Verifier without plan self-calibration | 63% | 57% | 22% | 9 of 123 (7%) | 114 of 123 | $0.0065 |
| Verifier: SAM 3.1 + Muse Spark (ours) | 72% | 70% | 51% | 15 of 123 (12%) | 108 of 123 | $0.0054 |
| Hard hand gate (previous product) | 66% | 61% | 31% | 10 of 123 (8%) | 47 of 123 | $0.0049 |
| Product: soft actor gate + SAM 3.1 + Muse Spark | 72% | 70% | 51% | 15 of 123 (12%) | 68 of 123 | $0.0076 |

### Correct per case

| System | done | done_dim | incomplete | missing_part | wrong_colour |
|---|---|---|---|---|---|
| spark_only | 31/44 | 23/44 | 38/44 | 37/41 | 22/38 |
| verify | 23/44 | 22/44 | 40/44 | 39/41 | 29/38 |
| sam_only | 39/44 | 39/44 | 20/44 | 36/41 | 19/38 |
| full | 14/44 | 13/44 | 43/44 | 39/41 | 31/38 |
| soft | 23/44 | 22/44 | 40/44 | 39/41 | 29/38 |
| verify_uncalibrated | 10/44 | 9/44 | 41/44 | 41/41 | 32/38 |

### Step segmentation (Muse Spark plan vs independent labels)

- labelled_steps: 65
- tutorials: 7
- mean_tiou: 0.488
- recall_at_0.3: 0.662
- recall_at_0.5: 0.462
- median_start_error_s: 1.0
- median_end_error_s: 2.5
- mean_abs_step_count_error: 3.57

### Latency (unpaced, one check at a time)

{'verify': {'n': 12, 'p50_s': 2.18, 'p90_s': 4.98}, 'full': {'n': 12, 'p50_s': 2.0, 'p90_s': 2.39}}
