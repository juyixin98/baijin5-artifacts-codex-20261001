"""seqdist: p-distance and restricted-substitution-model corrected distances
for synthetic aligned sequence pairs.

Module map:
    parsing     -- synthetic sequence / FASTA parsing and alphabet validation
    models      -- substitution model registry with stated assumptions
    distance    -- domain algorithms: site classification, p-distance, corrections
    bootstrap   -- site-resampling confidence intervals with a fixed random source
    provenance  -- SQLite run records (run id, inputs hash, intermediates, verdicts)
    service     -- orchestration: parse -> classify -> correct -> bootstrap -> record
    api         -- FastAPI validation interface (thin layer over service)
    errors      -- typed error taxonomy shared across all boundaries
"""

__version__ = "0.1.0"
