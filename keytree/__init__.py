"""keytree — local test root key derivation tree service.

Module boundaries:
- encoding:   unambiguous TLV protocol encoding for labels (no crypto).
- crypto:     HKDF adapters over mature backends (cryptography / PyCryptodome).
- identity:   key identity model; identity is never derived from display names.
- store:      SQLite state (root, registry, name bindings) and audit log.
- service:    derivation tree orchestration, run-scoped diagnostic logging.
- api:        FastAPI HTTP boundary mapping error categories to status codes.
"""

__version__ = "0.1.0"
