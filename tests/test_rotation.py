"""Index-key rotation: dual-version querying during an interrupted rotation
must not miss records; completion must retire the old version cleanly."""
import pytest

from sensitive_layer.errors import RotationConflictError

EMAIL = "lookup:email"
VALUES = {f"r{i}": f"user{i}@example.com" for i in range(5)}


def _seed(service):
    for rid, value in VALUES.items():
        service.put_record(request_id="seed", record_id=rid, field="email",
                           purpose=EMAIL, value=value)
    service.put_record(request_id="seed", record_id="rnull", field="email",
                       purpose=EMAIL, value=None)


def _all_found(service):
    for rid, value in VALUES.items():
        result = service.query(request_id="q", field="email",
                               purpose=EMAIL, value=value)
        assert result["confirmed"] == [rid], f"{rid} missing: {result}"


def test_interrupted_rotation_misses_nothing(service):
    _seed(service)
    status = service.begin_rotation(request_id="rot", crash_after=2)
    assert status["rotation"]["status"] == "rotating"
    assert status["rotation"]["processed"] == 2
    assert status["query_index_versions"] == [1, 2]

    # Only 2 of 6 records have v2 indexes; the rest are v1-only. Dual-version
    # queries must still find every record.
    _all_found(service)

    # Writes during rotation dual-write both index versions.
    service.put_record(request_id="t", record_id="rnew", field="email",
                       purpose=EMAIL, value="new@example.com")
    rec = service.get_record(request_id="t", record_id="rnew", field="email",
                             purpose=EMAIL)
    assert rec["index_versions"] == [1, 2]

    status = service.resume_rotation(request_id="rot")
    assert status["rotation"]["status"] == "idle"
    assert status["query_index_versions"] == [2]
    _all_found(service)
    result = service.query(request_id="q", field="email",
                           purpose=EMAIL, value="new@example.com")
    assert result["confirmed"] == ["rnew"]

    # Old-version index rows are fully retired.
    for rid in list(VALUES) + ["rnew"]:
        rec = service.get_record(request_id="t", record_id=rid, field="email",
                                 purpose=EMAIL)
        assert rec["index_versions"] == [2]


def test_double_begin_rejected(service):
    _seed(service)
    service.begin_rotation(request_id="rot", crash_after=1)
    with pytest.raises(RotationConflictError) as exc:
        service.begin_rotation(request_id="rot")
    assert exc.value.category == "rotation_conflict"


def test_resume_without_rotation_rejected(service):
    with pytest.raises(RotationConflictError):
        service.resume_rotation(request_id="rot")


def test_rotation_survives_restart(tmp_path, make_service):
    """Rotation state is persisted: a 'restarted' service on the same DB file
    resumes the interrupted rotation instead of losing track of it."""
    service = make_service()
    _seed(service)
    service.begin_rotation(request_id="rot", crash_after=1)

    restarted = make_service()  # same tmp_path -> same db file
    status = restarted.rotation_status()
    assert status["rotation"]["status"] == "rotating"
    assert status["query_index_versions"] == [1, 2]
    _all_found(restarted)
    done = restarted.resume_rotation(request_id="rot")
    assert done["rotation"]["status"] == "idle"
    _all_found(restarted)


def test_queries_during_rotation_use_both_versions(service):
    _seed(service)
    service.begin_rotation(request_id="rot", crash_after=1)
    result = service.query(request_id="q", field="email",
                           purpose=EMAIL, value=VALUES["r0"])
    assert result["index_versions_queried"] == [1, 2]
