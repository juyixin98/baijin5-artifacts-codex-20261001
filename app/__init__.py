"""Local Paillier ciphertext aggregation test service.

Layered structure:
- encoding.py        protocol encoding (signed integers, explicit bounds)
- crypto_adapter.py  thin adapter over the mature `phe` Paillier library
- storage.py         SQLite state (batches, contributions, aggregates)
- audit.py           audit trail with run identity
- service.py         batch lifecycle and aggregation orchestration
- verifier.py        independent verification (plaintext + ciphertext reference)
- main.py            FastAPI HTTP surface
"""

__version__ = "0.1.0"
