"""Minimizer seed index for synthetic reads.

Layered layout:
- app.sequence   : synthetic sequence parsing / validation
- app.minimizer  : domain algorithm (canonical k-mers, window minimizers)
- app.index      : SQLite-backed seed index + candidate query
- app.provenance : run identity, versions, JSONL step logging
- app.service    : FastAPI verification interface
"""

__version__ = "0.1.0"
