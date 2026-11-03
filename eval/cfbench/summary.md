## CF-Bench results

| System | Acc. | Bal. acc. | Done recall | False advance | Mistakes flagged | $/check |
|---|---|---|---|---|---|---|
| Pixel similarity to end frame (LOTO threshold) | 175 of 303 (58%) | | | 87 of 177 | | $0 |
| Muse Spark only (whole frame, VLM-as-judge) | 64% | 62% | 48% | 43 of 177 (24%) | 134 of 177 | $0.0025 |
| SAM 3.1 measured checks only | 64% | 68% | 88% | 94 of 177 (53%) | 83 of 177 | $0.0038 |
| Verifier: SAM 3.1 + Muse Spark (ours) | 70% | 70% | 68% | 49 of 177 (28%) | 128 of 177 | $0.0051 |
| Hard hand gate (previous product) | 69% | 69% | 64% | 48 of 177 (27%) | 91 of 177 | $0.0078 |
| Product: soft actor gate + SAM 3.1 + Muse Spark | 70% | 70% | 68% | 49 of 177 (28%) | 90 of 177 | $0.0076 |

### Correct per case

| System | done | done_dim | incomplete | missing_part | wrong_colour |
|---|---|---|---|---|---|
| full | 40/63 | 41/63 | 47/63 | 50/58 | 32/56 |
| verify | 44/63 | 41/63 | 46/63 | 51/58 | 31/56 |
| spark_only | 30/63 | 30/63 | 55/63 | 51/58 | 28/56 |
| sam_only | 56/63 | 55/63 | 16/63 | 44/58 | 23/56 |
| soft | 44/63 | 41/63 | 46/63 | 51/58 | 31/56 |

### Latency (unpaced, one check at a time)

None
