# RD replication report

Generated: 2026-09-28T08:58:20.033703+00:00
Versions: {"app": "1.0.0", "python": "3.12.3", "numpy": "2.4.6", "scipy": "1.15.3", "fastapi": "0.141.1"}

## Monte Carlo

| scenario | reps | bias | RMSE | MC SD | mean SE | SE/SD | coverage 95% | reject @5% | unidentified |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| sharp_jump | 200 | 0.0128 | 0.1724 | 0.1723 | 0.1677 | 0.973 | 0.950 | 1.000 | 0 |
| no_jump | 200 | 0.0127 | 0.1724 | 0.1724 | 0.1677 | 0.973 | 0.950 | 0.050 | 0 |

## Bandwidth sweep (sharp_jump, n=4000, true tau=3)

| h | tau | SE | eff N (L/R) | window span (L/R) |
|---:|---:|---:|---:|---:|
| 0.100 | 2.9472 | 0.1164 | 92.4/98.0 | 0.099/0.099 |
| 0.150 | 2.9805 | 0.0939 | 143.4/149.7 | 0.149/0.150 |
| 0.200 | 3.0082 | 0.0820 | 197.1/200.7 | 0.199/0.199 |
| 0.300 | 3.0538 | 0.0678 | 303.5/303.8 | 0.300/0.298 |
| 0.400 | 3.0732 | 0.0596 | 410.0/406.3 | 0.400/0.399 |

## Independent reference agreement

- core: 3.0173373886
- explicit weighted normal equations: 3.0173373886 (|diff|=2.66e-15)
- scipy.optimize BFGS WLS: 3.0173373728 (|diff|=1.57e-08)
- bootstrap-t p=0.0010, null-t KS p=0.160

## Diagnostic rates

- density-sorting flag rate: 0.940
- sparse-boundary unidentified rate: 1.000
