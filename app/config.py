"""Central configuration for the translation-estimation backend.

All numerical thresholds live here so that classification behaviour is
declared in one place and can be overridden per-request or via environment
variables (prefix ``OPP495_``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class KernelConfig:
    """Tunable parameters of the phase-correlation kernel and classifier."""

    # --- preprocessing -------------------------------------------------
    # Mean removal: declared and ON by default.  Both images share the same
    # zero-padded support, so a common mean (DC) convolves with the support's
    # sinc and injects a spurious correlation peak at zero lag; for
    # mean-dominated images (e.g. offset sinusoids) it hides the true peak.
    remove_mean: bool = True
    # Hann window: available and declared, but OFF by default.  Rationale
    # (measured on the fixture suite): with declared zero-padding the circular
    # wraparound is already handled exactly, while windowing (a) suppresses
    # overlap content that lies near the borders (large shifts), and (b) breaks
    # the exact tie between aliased peaks of periodic textures, which hides
    # their intrinsic ambiguity.  Enable for high-dynamic-range images where
    # spectral leakage from strong edges dominates.
    apply_window: bool = False       # separable Hann window on both images
    pad_factor: int = 2              # zero-pad to pad_factor * shape (declared,
                                     # extends unambiguous range to +/-n)

    # --- cross-power spectrum ------------------------------------------
    # Bins with |R| <= eps_rel * max|R| are explicitly zeroed: they carry no
    # reliable phase.  The default 1e-4 sits above the noise floor of 16-bit
    # quantization (~1e-4 relative); below-threshold bins would otherwise be
    # amplified to unit magnitude by phase normalisation and swamp the peak.
    eps_rel: float = 1e-4

    # --- flat-response detection ---------------------------------------
    flat_input_std: float = 1e-8     # input std below this -> constant image
    flat_surface_ratio: float = 1e-3 # std(surface)/|mean(surface)| below -> flat

    # --- subpixel refinement -------------------------------------------
    # (grid_size, step_px) rounds; the first round must cover +/-0.5 px, the
    # worst-case distance from the integer peak to the true sub-pixel peak.
    subpixel_steps: tuple = ((5, 0.25), (3, 0.05), (3, 0.01))

    # --- ambiguity / candidates ----------------------------------------
    ambiguity_ratio: float = 0.8     # secondary peak >= ratio * main -> ambiguous
    max_candidates: int = 8
    candidate_mask_radius: int = 3   # neighbourhood masked around each peak

    # --- confidence -----------------------------------------------------
    psr_exclude_radius: int = 5      # peak neighbourhood excluded for PSR stats

    # --- geometric validity --------------------------------------------
    min_overlap: float = 0.3         # overlap fraction below -> LOW_OVERLAP
    border_margin: int = 2           # |shift| >= n - margin -> PEAK_AT_BORDER

    # --- overlap verification ------------------------------------------
    ncc_threshold: float = 0.6       # zero-mean NCC in overlap below -> no content
    brightness_gain_tol: float = 0.05    # |gain-1| above -> brightness change
    brightness_offset_tol_frac: float = 0.02  # |offset|/range above -> change

    def with_overrides(self, **kw) -> "KernelConfig":
        """Return a new config with selected fields replaced (immutable update)."""
        return replace(self, **{k: v for k, v in kw.items() if v is not None})

    @staticmethod
    def from_env() -> "KernelConfig":
        cfg = KernelConfig()
        mapping = {
            "OPP495_PAD_FACTOR": ("pad_factor", int),
            "OPP495_MIN_OVERLAP": ("min_overlap", float),
            "OPP495_NCC_THRESHOLD": ("ncc_threshold", float),
            "OPP495_AMBIGUITY_RATIO": ("ambiguity_ratio", float),
        }
        overrides = {}
        for env, (field_name, cast) in mapping.items():
            raw = os.environ.get(env)
            if raw is not None:
                overrides[field_name] = cast(raw)
        return cfg.with_overrides(**overrides) if overrides else cfg


DEFAULT_CONFIG = KernelConfig()
