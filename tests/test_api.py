"""HTTP API tests: concrete responses, provenance recording, request ids."""

from __future__ import annotations

from . import reference_answers as ref


def test_health_lists_transcripts(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["transcripts"] == ["txA", "txB", "txC"]


def test_genomic_point_endpoint_exact_value(client):
    resp = client.post(
        "/map/genomic-to-transcript",
        json={"transcript_id": "txB", "position": 99},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OK"
    assert body["mapped"] == 0  # hand-computed: txB t=0 <-> g=99
    assert body["request_id"]


def test_intronic_point_endpoint_rejects_with_reason(client):
    resp = client.post(
        "/map/genomic-to-transcript",
        json={"transcript_id": "txA", "position": 20},
    )
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert body["reason"] == "INTRONIC"
    assert body["mapped"] is None


def test_transcript_interval_endpoint_fragment_split(client):
    resp = client.post(
        "/map/transcript-interval",
        json={"transcript_id": "txB", "start": 18, "end": 22},
    )
    body = resp.json()
    assert body["status"] == "OK"
    assert body["fragments"] == ref.TXB_T_INTERVAL_18_22["fragments"]
    assert body["mapped_length"] == 4


def test_roundtrip_validation_endpoint(client):
    resp = client.post(
        "/validate/roundtrip",
        json={"transcript_id": "txA", "space": "genomic", "position": 44},
    )
    body = resp.json()
    assert body["status"] == "OK"
    assert body["identity"] is True
    assert body["forward"]["mapped"] == 24  # hand-computed
    assert body["backward"]["mapped"] == 44

    resp = client.post(
        "/validate/roundtrip",
        json={"transcript_id": "txA", "space": "genomic", "position": 25},
    )
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert body["identity"] is False  # intronic: no round trip possible


def test_sequence_endpoint_hand_derived(client):
    resp = client.get("/transcripts/txB/sequence")
    body = resp.json()
    assert body["status"] == "OK"
    assert body["sequence"] == ref.TXB_SPLICED
    assert body["length"] == 40


def test_request_id_header_is_honored_and_provenance_recorded(client):
    resp = client.post(
        "/map/genomic-to-transcript",
        json={"transcript_id": "txA", "position": 10},
        headers={"X-Request-ID": "test-req-001"},
    )
    assert resp.json()["request_id"] == "test-req-001"

    prov = client.get("/provenance/test-req-001")
    assert prov.status_code == 200
    records = prov.json()["records"]
    assert len(records) == 1
    rec = records[0]
    assert rec["endpoint"] == "genomic_to_transcript"
    assert rec["status"] == "OK"
    assert rec["reason"] == "OK"
    assert rec["transcript_id"] == "txA"
    assert len(rec["input_sha256"]) == 64


def test_provenance_records_rejections_too(client):
    resp = client.post(
        "/map/genomic-to-transcript",
        json={"transcript_id": "txA", "position": 20},
        headers={"X-Request-ID": "test-req-002"},
    )
    assert resp.json()["reason"] == "INTRONIC"
    records = client.get("/provenance/test-req-002").json()["records"]
    assert records[0]["status"] == "REJECTED"
    assert records[0]["reason"] == "INTRONIC"


def test_provenance_unknown_request_id_empty(client):
    assert client.get("/provenance/nope").json()["records"] == []


def test_invalid_payload_returns_422(client):
    resp = client.post("/map/genomic-to-transcript", json={"transcript_id": "txA"})
    assert resp.status_code == 422
