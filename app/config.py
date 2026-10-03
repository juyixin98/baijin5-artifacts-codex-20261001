"""Service-wide constants: resource limits and numeric thresholds.

All values are plain module constants (no env overrides) so tests and the
service share exactly one source of truth.
"""

# --- Resource limits (violations map to RESOURCE_EXHAUSTED) ---
MAX_STREAMS = 1024
MAX_CHANNELS = 64
MAX_SECTIONS = 64
MAX_CHUNK_SAMPLES = 1_000_000

# --- Numeric thresholds (violations map to INPUT_ERROR) ---
# |a0| below this is treated as zero: the section cannot be normalized.
A0_ABS_TOL = 1e-300
# A section is stable only if every pole satisfies |p| < POLE_RADIUS_LIMIT.
# The limit is exactly 1.0: poles arbitrarily close to the unit circle are
# accepted, poles on or outside it are rejected.
POLE_RADIUS_LIMIT = 1.0
