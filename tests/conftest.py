import json
from pathlib import Path

import pytest

from app.config import Settings
from app.models import PhaseRequest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        app_name="haplotype-phasing-service-test",
        max_enum_sites=16,
        default_quality=10,
        max_quality=60,
        max_reported_solutions=8,
        db_path=str(tmp_path / "provenance.db"),
    )


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def request_from_fixture(name: str) -> PhaseRequest:
    return PhaseRequest(**load_fixture(name))
