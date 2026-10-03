"""Version identifiers surfaced in API responses, logs and provenance records."""

APP_VERSION = "1.0.0"

# Bumped whenever the scoring / p-value semantics change, so stored provenance
# records can be interpreted against the algorithm that produced them.
ALGORITHM_VERSION = "pwm-exact-v1"
