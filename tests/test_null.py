"""NULL semantics: stored as NULL ciphertext, never indexed, never queryable."""
import pytest

from sensitive_layer.errors import NullNotIndexableError


def test_null_stored_without_index(service):
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value=None)
    rec = service.get_record(request_id="t", record_id="r1", field="email",
                             purpose="lookup:email", include_plaintext=True)
    assert rec["is_null"] is True
    assert rec["value"] is None
    assert rec["index_versions"] == []


def test_null_query_rejected_with_typed_error(service):
    with pytest.raises(NullNotIndexableError) as exc:
        service.query(request_id="t", field="email",
                      purpose="lookup:email", value=None)
    assert exc.value.category == "null_not_indexable"


def test_null_record_not_matched_by_any_query(service):
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value=None)
    service.put_record(request_id="t", record_id="r2", field="email",
                       purpose="lookup:email", value="alice@example.com")
    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value="alice@example.com")
    assert result["confirmed"] == ["r2"]


def test_overwrite_with_null_removes_index(service):
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value="alice@example.com")
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value=None)
    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value="alice@example.com")
    assert result["confirmed"] == []
    assert result["filtered_candidates"] == 0  # index row is gone, not just stale
    rec = service.get_record(request_id="t", record_id="r1", field="email",
                             purpose="lookup:email")
    assert rec["is_null"] is True
    assert rec["index_versions"] == []
