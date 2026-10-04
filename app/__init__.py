"""Rule-based enzymatic digest fragment enumeration service.

Synthetic-protein digest service: sequence parsing, explicit cleavage /
blocking-context rules, missed-cleavage enumeration, position-preserving
provenance, and monoisotopic mass tables with a strict distinction between
unknown residues and bounded (ambiguous) mass states.
"""

__version__ = "1.0.0"
MASS_TABLE_VERSION = "mono-residue-v1"
ENZYME_CATALOG_VERSION = "synthetic-enzymes-v1"
