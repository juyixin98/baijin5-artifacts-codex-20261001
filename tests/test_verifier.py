"""Independent verifier: accepts honest evidence, flags every tampering."""

import copy

from commit_reveal.verify.verifier import verify_evidence

from tests.conftest import PARTICIPANTS, ROUND_ID, commit_all, make_round, reveal


def _honest_evidence(service, clock) -> dict:
    make_round(service)
    commit_all(service, clock)
    for pid in PARTICIPANTS:
        reveal(service, ROUND_ID, pid)
    return service.finalize("req-finalize", ROUND_ID)


def test_honest_evidence_verifies(service, clock):
    report = verify_evidence(_honest_evidence(service, clock))
    assert report.valid
    assert {c.name for c in report.checks} == {
        "structure", "version", "binding", "completeness", "seed", "draw", "winner",
    }


def test_tampered_winner_detected(service, clock):
    evidence = _honest_evidence(service, clock)
    others = [p for p in PARTICIPANTS if p != evidence["draw"]["winner"]]
    evidence["draw"]["winner"] = others[0]
    report = verify_evidence(evidence)
    assert not report.valid
    assert any(c.name == "winner" and not c.ok for c in report.checks)


def test_tampered_seed_detected(service, clock):
    evidence = _honest_evidence(service, clock)
    evidence["seed"] = "00" * 32
    report = verify_evidence(evidence)
    assert not report.valid
    assert any(c.name == "seed" and not c.ok for c in report.checks)


def test_tampered_reveal_detected(service, clock):
    evidence = _honest_evidence(service, clock)
    evidence["reveals"][0]["random_value"] = "00" * 32
    report = verify_evidence(evidence)
    assert not report.valid
    assert any(c.name == "binding" and not c.ok for c in report.checks)


def test_dropped_unrevealed_listing_detected(service, clock):
    make_round(service)
    commit_all(service, clock)
    reveal(service, ROUND_ID, "alice")
    reveal(service, ROUND_ID, "bob")
    from tests.conftest import REVEAL_DEADLINE
    clock.advance_to(REVEAL_DEADLINE)
    evidence = service.finalize("req-finalize", ROUND_ID)

    assert evidence["unrevealed_commitments"] == ["carol"]
    tampered = copy.deepcopy(evidence)
    tampered["unrevealed_commitments"] = []  # pretend carol never committed
    report = verify_evidence(tampered)
    assert not report.valid
    assert any(c.name == "completeness" and not c.ok for c in report.checks)


def test_missing_field_detected(service, clock):
    evidence = _honest_evidence(service, clock)
    del evidence["seed"]
    report = verify_evidence(evidence)
    assert not report.valid
    assert report.checks[0].name == "structure"
