"""Central configuration: numeric bounds and capacity limits.

Learning-rate policy
--------------------
Both LMS and NLMS accept ``mu`` in the open interval (MU_MIN, MU_MAX) =
(0, 2). For NLMS this is the classic stability range for the normalized
update. For LMS, stability additionally depends on reference-signal power
(roughly ``mu < 2 / (L * E[x^2])``); the backend enforces the absolute
(0, 2) bound and documents that LMS convergence is only guaranteed when
the input-power condition also holds. Values outside the range are
rejected as ``input_error`` before any state is touched.

Frozen adaptation
-----------------
Adaptation can be frozen per sample via ``freeze_intervals`` (half-open
``[start, end)`` sample indices within a processed block). While frozen,
the filter still computes output/error from the *current* weights but
skips the weight update entirely, so weights are bit-identical across a
frozen interval.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Learning-rate bounds (open interval).
    mu_min: float = 1e-6
    mu_max: float = 2.0 - 1e-6

    # NLMS energy regularization: denominator = eps + ||x||^2.
    # eps must be strictly positive so a silent (all-zero) reference can
    # never cause a division by zero.
    eps_min: float = 1e-12
    eps_default: float = 1e-8

    # Structural limits.
    filter_len_min: int = 1
    filter_len_max: int = 512
    max_channels: int = 64
    max_block_samples: int = 200_000
    max_eval_samples: int = 500_000


DEFAULT_SETTINGS = Settings()
