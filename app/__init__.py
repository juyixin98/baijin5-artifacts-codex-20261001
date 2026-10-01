"""LORD 3 online FDR teaching service.

Modules:
    errors:     typed, categorized error contract shared by every layer.
    contracts:  the frozen statistical rule (constants, formulas, hashes).
    lord3:      pure estimation kernel, no I/O.
    store:      append-only SQLite evidence store with hash chains.
    diagnostics: replay / evidence verification and FDP statistics.
    simulation: local synthetic p-value streams.
    service:    FastAPI application factory and HTTP boundary.
"""

__version__ = "1.0.0"
