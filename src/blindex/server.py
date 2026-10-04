"""按环境变量组装应用（uvicorn blindex.server:app）。

环境变量：
- BLINDEX_KEYFILE  密钥环文件路径（默认 config/dev_keys.json）
- BLINDEX_DB       SQLite 路径（默认 data/blindex.db）
"""
from __future__ import annotations

import os

from .api import create_app
from .audit import AuditLog
from .config import load_keyring
from .crypto_adapter import CryptoAdapter
from .service import BlindIndexService
from .storage import Storage
from .verify import IndependentVerifier

KEYFILE = os.environ.get("BLINDEX_KEYFILE", "config/dev_keys.json")
DB_PATH = os.environ.get("BLINDEX_DB", "data/blindex.db")

keyring = load_keyring(KEYFILE)
storage = Storage(DB_PATH)
adapter = CryptoAdapter(keyring)
audit = AuditLog(storage)
service = BlindIndexService(storage, adapter, audit, keyfile=KEYFILE)
verifier = IndependentVerifier(keyring.domain, keyring.index_bits, keyring.index_keys)

app = create_app(service, storage, audit, verifier)
