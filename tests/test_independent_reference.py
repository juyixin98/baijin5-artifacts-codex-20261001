"""Independent end-to-end verification: the service's answers are checked
against the stdlib plaintext reference (verification/reference.py) and the
generated fixtures — neither of which is produced by the code under test."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sensitive_layer.api.main import create_app
from verification import reference

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def verify_client(tmp_path):
    base_cfg = json.loads((ROOT / "config/settings.verify.json").read_text())
    base_cfg["database_path"] = str(tmp_path / "verify.db")
    base_cfg["audit_log_path"] = str(tmp_path / "audit.log")
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps(base_cfg))
    return TestClient(create_app(str(cfg_path)))


def test_independent_reference_verification(verify_client):
    fixtures = json.loads((ROOT / "verification/fixtures.json").read_text())
    report = reference.run_verification(verify_client, fixtures)
    failures = [c for c in report["checks"] if not c["ok"]]
    assert failures == [], f"verification failures: {failures}"
    # Sanity: the run actually exercised every check category.
    names = {c["name"].split(":")[0] for c in report["checks"]}
    assert "put" in names
    assert "index-probe" in names
    assert any(c["name"].startswith("mid-rotation:") for c in report["checks"])
    assert any(c["name"].startswith("post-rotation:") for c in report["checks"])
    assert "null-query-rejected" in {c["name"] for c in report["checks"]}
