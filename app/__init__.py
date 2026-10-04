"""Segmented AEAD service (SSEA).

Modules have distinct responsibilities:

* :mod:`app.core.protocol`  - wire framing, nonce derivation, AAD encoding
* :mod:`app.core.crypto`    - mature AEAD backends (cryptography / PyCryptodome)
* :mod:`app.core.keyring`    - local key material management
* :mod:`app.db.store`       - SQLite stream/segment/audit persistence
* :mod:`app.core.staging`   - permission-controlled fragment staging & release
* :mod:`app.core.audit`     - structured, redaction-safe diagnostics
* :mod:`app.core.service`   - stream state machine (the policy layer)
* :mod:`app.api.server`     - FastAPI transport
"""

__version__ = "1.0.0"
