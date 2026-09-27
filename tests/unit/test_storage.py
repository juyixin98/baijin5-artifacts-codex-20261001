"""Evidence store tests."""

from __future__ import annotations

import pytest

from defeasible.errors import InvalidInputError
from defeasible.language import Term, Theory
from defeasible.storage import EvidenceStore


@pytest.fixture
def store() -> EvidenceStore:
    return EvidenceStore(":memory:")


def test_case_lifecycle(store: EvidenceStore) -> None:
    store.create_case("c1", "first case")
    with pytest.raises(InvalidInputError):
        store.create_case("c1")  # duplicate
    store.ensure_case("c1")     # idempotent
    ids = [c["case_id"] for c in store.list_cases()]
    assert ids == ["c1"]


def test_unknown_case_rejected(store: EvidenceStore) -> None:
    with pytest.raises(InvalidInputError):
        store.add_evidence("ghost", ["bird(tweety)"])


def test_evidence_round_trip_and_dedup(store: EvidenceStore) -> None:
    store.ensure_case("c")
    n = store.add_evidence("c", ["bird(tweety)", "penguin(polly)"])
    assert n == 2
    store.add_evidence("c", ["bird(tweety)"])  # duplicate ignored
    loaded = store.load_evidence("c")
    assert sorted(t.literal for t in loaded) == [
        "bird(tweety)",
        "penguin(polly)",
    ]


def test_non_ground_evidence_rejected(store: EvidenceStore) -> None:
    store.ensure_case("c")
    with pytest.raises(InvalidInputError):
        store.add_evidence("c", [Term.parse("bird(X)")])


def test_store_does_not_invent_negations(store: EvidenceStore) -> None:
    # Storing P must never make -P retrievable: absence is not opposition.
    store.ensure_case("c")
    store.add_evidence("c", ["bird(tweety)"])
    lits = {t.literal for t in store.load_evidence("c")}
    assert "bird(tweety)" in lits
    assert "-bird(tweety)" not in lits


def test_theory_persistence(store: EvidenceStore) -> None:
    store.ensure_case("c")
    t = Theory()
    t.add_rule("r", "default", ["bird(X)"], "flies(X)")
    store.save_theory("c", t.to_dict())
    loaded = store.load_theory("c")
    assert loaded is not None
    assert Theory.from_dict(loaded).rules[0].id == "r"
    assert store.load_theory("missing") is None


def test_delete_case_cascades(store: EvidenceStore) -> None:
    store.ensure_case("c")
    store.add_evidence("c", ["p(t)"])
    store.delete_case("c")
    with pytest.raises(InvalidInputError):
        store.load_evidence("c")
