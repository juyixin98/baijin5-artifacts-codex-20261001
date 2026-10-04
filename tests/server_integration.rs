//! End-to-end HTTP tests: two concurrent parties against a live server,
//! plus state-machine and diagnostics checks.

mod common;

use std::time::Duration;

use psi_dh::client::{Client, ClientError};
use psi_dh::verify::plaintext_intersection;

#[tokio::test]
async fn full_http_roundtrip_matches_plaintext_reference() {
    let base = common::spawn_server().await;
    let session = {
        let base = base.clone();
        common::blocking(move || Client::new(&base).new_session().unwrap()).await
    };

    let a_elements = common::elements("alice", 0..40);
    let b_elements = {
        let mut v = common::elements("alice", 20..60);
        v.extend(common::elements("bob", 0..10));
        v
    };

    let b_base = base.clone();
    let b_session = session.clone();
    let b_elems = b_elements.clone();
    let b_handle = std::thread::spawn(move || Client::new(&b_base).run_party_b(&b_session, &b_elems));

    let got = {
        let base = base.clone();
        let session = session.clone();
        let a_elements = a_elements.clone();
        common::blocking(move || Client::new(&base).run_party_a(&session, &a_elements).unwrap())
            .await
    };
    b_handle.join().unwrap().unwrap();

    let reference = plaintext_intersection(&a_elements, &b_elements);
    assert_eq!(got, reference);
    assert_eq!(got, common::elements("alice", 20..40));

    // Audit: session_created + a_submission + b_response, all accepted.
    let records = {
        let base = base.clone();
        let session = session.clone();
        common::blocking(move || Client::new(&base).audit(&session).unwrap()).await
    };
    assert_eq!(records.len(), 3);
    assert!(records.iter().all(|r| r.outcome == "accepted"));
    assert_eq!(records[1].event, "a_submission");
    assert!(records[1].detail.contains("points=40"));
    assert_eq!(records[2].event, "b_response");
    assert!(records[2].detail.contains("b_points=50"));
    // Request ids are present for traceability.
    assert!(records.iter().all(|r| !r.request_id.is_empty()));
}

#[tokio::test]
async fn b_cannot_respond_before_a_submits() {
    let base = common::spawn_server().await;
    let session = {
        let base = base.clone();
        common::blocking(move || Client::new(&base).new_session().unwrap()).await
    };
    let http = reqwest::Client::new();

    let resp = http
        .post(format!("{base}/v1/sessions/{session}/submissions/b"))
        .json(&serde_json::json!({ "b_blinded": [], "a_doubly": [] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 409);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "INVALID_SESSION_STATE");
    assert_eq!(body["error"]["session_state"], "created");
}

#[tokio::test]
async fn unknown_session_is_404_with_code() {
    let base = common::spawn_server().await;
    let http = reqwest::Client::new();
    let resp = http
        .get(format!("{base}/v1/sessions/{}/messages/a", "00".repeat(32)))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 404);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "SESSION_NOT_FOUND");
}

#[tokio::test]
async fn count_mismatch_is_rejected_with_specific_code() {
    let base = common::spawn_server().await;
    let session = {
        let base = base.clone();
        common::blocking(move || Client::new(&base).new_session().unwrap()).await
    };

    // A submits 2 elements via raw HTTP so we control B's malformed response.
    let a_elements = common::elements("x", 0..2);
    let (party_a, _) = psi_dh::protocol::PartyA::new(
        {
            let mut id = [0u8; 32];
            id.copy_from_slice(&hex::decode(&session).unwrap());
            id
        },
        &a_elements,
    );
    let blinded: Vec<String> = party_a.blinded_message().iter().map(hex::encode).collect();
    let http = reqwest::Client::new();
    let resp = http
        .post(format!("{base}/v1/sessions/{session}/submissions/a"))
        .json(&serde_json::json!({ "points": blinded }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);

    // B responds with only 1 a_doubly entry for A's 2 points.
    let good = hex::encode(psi_dh::crypto::encode_point(
        &(psi_dh::crypto::generate_blinding_scalar()
            * psi_dh::crypto::hash_to_point(&[5u8; 32], b"filler")),
    ));
    let resp = http
        .post(format!("{base}/v1/sessions/{session}/submissions/b"))
        .json(&serde_json::json!({
            "b_blinded": [good.clone()],
            "a_doubly": [good],
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "COUNT_MISMATCH");
    assert_eq!(body["error"]["session_state"], "a_submitted");
}

#[tokio::test]
async fn client_surfaces_server_diagnostics() {
    let base = common::spawn_server().await;

    // Party A against a session that does not exist: the client error must
    // carry the server's code and request id.
    let err = common::blocking(move || {
        Client::new(&base)
            .with_polling(Duration::from_millis(50), Duration::from_secs(2))
            .run_party_a(&"ab".repeat(32), &common::elements("e", 0..1))
            .unwrap_err()
    })
    .await;
    match err {
        ClientError::Server {
            status,
            code,
            request_id,
            ..
        } => {
            assert_eq!(status, 404);
            assert_eq!(code, "SESSION_NOT_FOUND");
            assert!(!request_id.is_empty());
        }
        other => panic!("expected server error, got {other:?}"),
    }
}
