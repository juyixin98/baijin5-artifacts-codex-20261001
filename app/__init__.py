"""Allelic haplotype assembly backend (synthetic diploid phasing)."""

__version__ = "0.1.0"

# Bumped whenever the phasing objective, cost rule, or block logic changes.
# Recorded in every provenance row so results can be traced to the exact
# algorithm that produced them.
ALGORITHM_VERSION = "mec-exact-enum-1"
