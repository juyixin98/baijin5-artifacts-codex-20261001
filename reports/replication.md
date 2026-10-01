# 2SLS replication report

reps per experiment: 400, truth beta = 0.75

## E1 Consistency (IV vs OLS)
| scenario | IV mean | IV bias | IV RMSE | OLS mean | OLS bias | flagged weak |
|---|---|---|---|---|---|---|
| strong_iv_n1000 | 0.750 | +0.000 | 0.038 | 1.100 | +0.350 | 0.00 |
| strong_iv_n4000 | 0.752 | +0.002 | 0.019 | 1.100 | +0.350 | 0.00 |
| weak_iv_n4000 | 0.753 | +0.003 | 0.410 | 1.348 | +0.598 | 0.93 |

## E2 95% CI coverage
| setting | coverage | mean SE |
|---|---|---|
| coverage_homoskedastic_homoskedastic | 0.950 | 0.0438 |
| coverage_homoskedastic_robust | 0.948 | 0.0438 |
| coverage_heteroskedastic_homoskedastic | 0.863 | 0.0626 |
| coverage_heteroskedastic_robust | 0.940 | 0.0808 |

## E3 Sargan size and power
| experiment | rejection @5% |
|---|---|
| sargan_size | 0.058 |
| sargan_power | 1.000 |

## E4 Endogeneity test
| experiment | rejection @5% |
|---|---|
| dwh_power_endogenous | 1.000 |
| dwh_size_exogenous | 0.043 |

## E5 Failure categories
| fixture | expected | observed | ok |
|---|---|---|---|
| category_rank_failure | unidentified | unidentified | YES |
| category_duplicate_instrument | unidentified | unidentified | YES |
| category_population_zero_pi | weak | weak | YES |
| category_weak | weak | weak | YES |
| category_collinear | ok | ok | YES |
