"""Local multi-party commit-reveal protocol with deterministic seed draw.

Modules:
- protocol: canonical encoding, typed errors
- crypto:   mature-library adapters (cryptography / PyCryptodome)
- state:    SQLite store, round state machine, audit log
- draw:     deterministic draw from combined seed
- verify:   independent recomputation of public evidence
- api:      FastAPI surface
"""

__version__ = "0.1.0"
