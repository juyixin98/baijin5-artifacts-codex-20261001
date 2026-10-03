"""HTTP API tests: concrete payloads, failure categories, request ids."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["x-request-id"]


def test_list_and_get_transcript(client):
    r = client.get("/transcripts")
    ids = [t["transcript_id"] for t in r.json()["transcripts"]]
    assert ids == ["T1_PLUS", "T2_MINUS", "T3_ADJACENT"]

    r = client.get("/transcripts/T2_MINUS")
    body = r.json()
    assert body["strand"] == "-"
    assert body["mature_length"] == 75
    assert body["exons"] == [[500, 530], [560, 585], [620, 640]]


def test_tx_to_genomic_point_plus(client):
    r = client.post("/map/tx-to-genomic/point", json={
        "transcript_id": "T1_PLUS", "position": 30
    })
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["genomic_position"] == 160
    assert result["exon_index"] == 1
    assert r.json()["status"] == "mapped"


def test_genomic_to_tx_point_minus_roundtrip(client):
    r = client.post("/map/genomic-to-tx/point", json={
        "transcript_id": "T2_MINUS", "position": 639
    })
    assert r.status_code == 200
    assert r.json()["result"]["tx_position"] == 0

    r2 = client.post("/map/tx-to-genomic/point", json={
        "transcript_id": "T2_MINUS", "position": 0
    })
    assert r2.json()["result"]["genomic_position"] == 639


def test_interval_split_across_exons(client):
    r = client.post("/map/tx-to-genomic/interval", json={
        "transcript_id": "T1_PLUS", "start": 25, "end": 55
    })
    body = r.json()["result"]
    assert body["fragment_count"] == 3
    assert body["mapped_length"] == 30
    assert [
        [f["genomic_start"], f["genomic_end"]] for f in body["fragments"]
    ] == [[125, 130], [160, 180], [210, 215]]
    total = sum(
        f["genomic_end"] - f["genomic_start"] for f in body["fragments"]
    )
    assert total == body["length"] == 30


def test_intronic_point_rejected_with_category(client):
    r = client.post("/map/genomic-to-tx/point", json={
        "transcript_id": "T1_PLUS", "position": 145
    })
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "intronic_position"
    assert err["key_state"]["prev_exon"] == [100, 130]
    assert err["key_state"]["next_exon"] == [160, 180]
    # the rejection still carries a request id for traceability
    assert r.json()["request_id"]
    assert r.headers["x-request-id"] == r.json()["request_id"]


def test_region_over_intron_rejected(client):
    r = client.post("/map/genomic-to-tx/interval", json={
        "transcript_id": "T1_PLUS", "start": 125, "end": 165
    })
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "region_not_mappable"
    assert r.json()["error"]["key_state"]["intronic_gaps"] == [[130, 160]]


def test_genomic_to_tx_interval_success_minus(client):
    # single-exon exonic interval on the minus strand: g[525,530) -> tx[45,50)
    r = client.post("/map/genomic-to-tx/interval", json={
        "transcript_id": "T2_MINUS", "start": 525, "end": 530,
        "request_id": "g2t-ok"
    })
    assert r.status_code == 200
    result = r.json()["result"]
    assert (result["tx_start"], result["tx_end"]) == (45, 50)
    assert result["fragments"][0]["genomic_start"] == 525
    # and it is retrievable from the audit trail
    trail = client.get("/audit/g2t-ok").json()
    assert trail["count"] == 1 and trail["records"][0]["status"] == "mapped"


def test_out_of_range_and_invalid_categories(client):
    r = client.post("/map/tx-to-genomic/point", json={
        "transcript_id": "T1_PLUS", "position": 80
    })
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "coordinate_out_of_range"

    r = client.post("/map/tx-to-genomic/interval", json={
        "transcript_id": "T1_PLUS", "start": 50, "end": 10
    })
    assert r.status_code == 422  # pydantic validation of ordering

    r = client.post("/map/tx-to-genomic/point", json={
        "transcript_id": "UNKNOWN", "position": 0
    })
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "transcript_not_found"


def test_schema_rejects_negative_and_wrong_type(client):
    r = client.post("/map/tx-to-genomic/point", json={
        "transcript_id": "T1_PLUS", "position": -1
    })
    assert r.status_code == 422
    r = client.post("/map/tx-to-genomic/point", json={
        "transcript_id": "T1_PLUS", "position": "3"
    })
    assert r.status_code == 422


def test_client_supplied_request_id_groups_audit(client):
    headers = {"x-request-id": "trace-xyz"}
    client.post("/map/tx-to-genomic/point",
                json={"transcript_id": "T1_PLUS", "position": 0,
                      "request_id": "trace-xyz"}, headers=headers)
    client.post("/map/genomic-to-tx/point",
                json={"transcript_id": "T1_PLUS", "position": 145,
                      "request_id": "trace-xyz"}, headers=headers)
    r = client.get("/audit/trace-xyz")
    body = r.json()
    assert body["count"] == 2
    statuses = {row["status"] for row in body["records"]}
    assert statuses == {"mapped", "rejected"}
    codes = {row["error_code"] for row in body["records"] if row["status"] == "rejected"}
    assert codes == {"intronic_position"}


def test_adjacent_exon_boundary_mappable(client):
    r = client.post("/map/genomic-to-tx/point", json={
        "transcript_id": "T3_ADJACENT", "position": 50
    })
    assert r.status_code == 200
    assert r.json()["result"]["tx_position"] == 10
