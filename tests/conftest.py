import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blindex.audit import AuditLog
from blindex.config import generate_keyring
from blindex.crypto_adapter import CryptoAdapter
from blindex.service import BlindIndexService
from blindex.storage import Storage
from blindex.verify import IndependentVerifier

from reference import FIXTURES

REQ = "test-request"


@pytest.fixture()
def keyring():
    return generate_keyring(domain="local-pii/v1", index_bits=64)


@pytest.fixture()
def storage(tmp_path):
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def service(storage, keyring):
    return BlindIndexService(storage, CryptoAdapter(keyring), AuditLog(storage))


@pytest.fixture()
def verifier(keyring):
    return IndependentVerifier(keyring.domain, keyring.index_bits, keyring.index_keys)


@pytest.fixture()
def loaded_service(service):
    """把明文夹具写入系统，返回 {夹具id: 系统record_id} 映射。"""
    id_map = {}
    for fx_id, fields in FIXTURES.items():
        id_map[fx_id] = service.create_record(fields, REQ)
    return service, id_map
