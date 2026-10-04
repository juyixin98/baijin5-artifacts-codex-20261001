//! Point-validation tests: malformed encodings must be rejected with the
//! specific error category, both at the crypto layer and over HTTP.

mod common;

use psi_dh::crypto::{decode_point, encode_point, generate_blinding_scalar, hash_to_point};
use psi_dh::error::PsiError;

#[test]
fn non_canonical_encoding_is_rejected() {
    // 0xff..ff is >= the field modulus, so it cannot be a canonical
    // ristretto255 encoding; decompress must fail.
    let err = decode_point(7, &[0xffu8; 32]).unwrap_err();
    assert_eq!(err, PsiError::InvalidPointEncoding { index: 7 });
}

#[test]
fn identity_point_is_rejected() {
    // All-zero is the canonical encoding of the identity: it decodes fine
    // but is forbidden by the protocol.
    let err = decode_point(3, &[0x00u8; 32]).unwrap_err();
    assert_eq!(err, PsiError::IdentityPoint { index: 3 });
}

#[test]
fn wrong_lengths_are_rejected() {
    assert_eq!(
        decode_point(0, &[0x11u8; 31]).unwrap_err(),
        PsiError::BadPointLength { index: 0, got: 31 }
    );
    assert_eq!(
        decode_point(1, &[0x11u8; 33]).unwrap_err(),
        PsiError::BadPointLength { index: 1, got: 33 }
    );
    assert_eq!(
        decode_point(2, &[]).unwrap_err(),
        PsiError::BadPointLength { index: 2, got: 0 }
    );
}

#[test]
fn honestly_produced_points_roundtrip() {
    let session = [9u8; 32];
    let scalar = generate_blinding_scalar();
    let point = scalar * hash_to_point(&session, b"roundtrip");
    let decoded = decode_point(0, &encode_point(&point)).expect("valid point must decode");
    assert_eq!(decoded, point);
}

#[tokio::test]
async fn server_rejects_invalid_points_with_specific_codes() {
    let base = common::spawn_server().await;
    let http = reqwest::Client::new();

    let session: serde_json::Value = http
        .post(format!("{base}/v1/sessions"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let session_id = session["session_id"].as_str().unwrap().to_string();

    // Non-canonical point -> 400 INVALID_POINT_ENCODING, with a request id.
    let resp = http
        .post(format!("{base}/v1/sessions/{session_id}/submissions/a"))
        .json(&serde_json::json!({ "points": [hex::encode([0xffu8; 32])] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    assert!(resp.headers().get("x-request-id").is_some());
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "INVALID_POINT_ENCODING");
    assert!(!body["error"]["request_id"].as_str().unwrap().is_empty());
    assert_eq!(body["error"]["session_state"], "created");

    // Identity point -> 400 IDENTITY_POINT.
    let resp = http
        .post(format!("{base}/v1/sessions/{session_id}/submissions/a"))
        .json(&serde_json::json!({ "points": [hex::encode([0x00u8; 32])] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "IDENTITY_POINT");

    // Truncated point -> 400 BAD_POINT_LENGTH.
    let resp = http
        .post(format!("{base}/v1/sessions/{session_id}/submissions/a"))
        .json(&serde_json::json!({ "points": [hex::encode([0x42u8; 16])] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "BAD_POINT_LENGTH");

    // Every rejection must be audited with its reason; the session must
    // still be usable afterwards (rejections don't corrupt state).
    let audit: serde_json::Value = http
        .get(format!("{base}/v1/sessions/{session_id}/audit"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let records = audit["records"].as_array().unwrap();
    let rejected: Vec<_> = records
        .iter()
        .filter(|r| r["outcome"] == "rejected")
        .collect();
    assert_eq!(rejected.len(), 3);
    assert!(rejected[0]["detail"]
        .as_str()
        .unwrap()
        .contains("INVALID_POINT_ENCODING"));
    assert!(rejected[1]["detail"].as_str().unwrap().contains("IDENTITY_POINT"));
    assert!(rejected[2]["detail"]
        .as_str()
        .unwrap()
        .contains("BAD_POINT_LENGTH"));
}
