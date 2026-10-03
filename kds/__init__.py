"""Local test root-key derivation tree service (KDS).

Modules and their contracts:

- ``errors``         – stable error taxonomy shared by every layer.
- ``encoding``       – unambiguous (injective) label encoding; the only
                       sanctioned way to turn labels into bytes.
- ``crypto_adapter`` – HKDF-SHA256 operations on vetted backends
                       (``cryptography`` primary, PyCryptodome as an
                       independent cross-check). No hash primitives are
                       implemented here.
- ``identity``       – structured key identity; ``key_id`` is derived from
                       the identity tuple, never from a display name.
- ``state``          – SQLite-backed key registry and audit log. Key
                       material never enters this layer.
- ``service``        – the derivation tree itself (root -> tenant ->
                       purpose -> version -> context -> output).
- ``api``            – FastAPI surface translating error categories to
                       HTTP status codes.
"""

__version__ = "0.1.0"
