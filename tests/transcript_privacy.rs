//! Transcript privacy: nothing on the wire or in the audit log may contain
//! raw elements. Scans actual HTTP response bodies, the exact request
//! payloads, and the audit records.

mod common;

use psi_dh::client::Client;
use psi_dh::crypto;
use psi_dh::protocol::{PartyA, PartyB};
use psi_dh::verify::find_leaked_elements;

#[test]
fn in_process_transcript_contains_no_raw_elements() {
    let session = crypto::generate_session_id();
    let a_elements = common::elements("alice-secret", 0..25);
    let b_elements = common::elements("bob-secret", 10..40);
    let all_elements: Vec<Vec<u8>> = a_elements
        .iter()
        .chain(b_elements.iter())
        .cloned()
        .collect();

    let (party_a, _) = PartyA::new(session, &a_elements);
    let a_msg = party_a.blinded_message();
    let a_blinded = crypto::decode_points(&a_msg).unwrap();
    let b_response = PartyB::respond(&session, &b_elements, &a_blinded);

    // Serialize the transcript exactly as it would cross the wire.
    let mut transcript = Vec::new();
    transcript.extend(
        serde_json::to_string(&a_msg.iter().map(hex::encode).collect::<Vec<_>>())
            .unwrap()
            .into_bytes(),
    );
    transcript.extend(
        serde_json::to_string(&b_response.b_blinded.iter().map(hex::encode).collect::<Vec<_>>())
            .unwrap()
            .into_bytes(),
    );
    transcript.extend(
        serde_json::to_string(&b_response.a_doubly.iter().map(hex::encode).collect::<Vec<_>>())
            .unwrap()
            .into_bytes(),
    );

    let leaked = find_leaked_elements(&transcript, &all_elements);
    assert!(leaked.is_empty(), "transcript leaked elements: {leaked:?}");
}

#[tokio::test]
async fn http_transcript_and_audit_contain_no_raw_elements() {
    let base = common::spawn_server().await;
    let session = {
        let base = base.clone();
        common::blocking(move || Client::new(&base).new_session().unwrap()).await
    };

    let a_elements = common::elements("alice-secret", 0..15);
    let b_elements = {
        // 10 shared with A (alice-secret-0005..0015), 10 B-only.
        let mut v = common::elements("alice-secret", 5..25);
        v.extend(common::elements("bob-secret", 0..10));
        v
    };
    let all_elements: Vec<Vec<u8>> = a_elements
        .iter()
        .chain(b_elements.iter())
        .cloned()
        .collect();

    let b_base = base.clone();
    let b_session = session.clone();
    let b_elems = b_elements.clone();
    let b_handle = std::thread::spawn(move || Client::new(&b_base).run_party_b(&b_session, &b_elems));
    let intersection = {
        let base = base.clone();
        let session = session.clone();
        common::blocking(move || Client::new(&base).run_party_a(&session, &a_elements).unwrap())
            .await
    };
    b_handle.join().unwrap().unwrap();
    assert_eq!(intersection.len(), 10);

    // Collect everything an observer of the server could see.
    let http = reqwest::Client::new();
    let mut transcript = Vec::new();
    for url in [
        format!("{base}/v1/sessions/{session}"),
        format!("{base}/v1/sessions/{session}/messages/a"),
        format!("{base}/v1/sessions/{session}/messages/b"),
        format!("{base}/v1/sessions/{session}/audit"),
    ] {
        let body = http.get(&url).send().await.unwrap().text().await.unwrap();
        transcript.extend(body.into_bytes());
    }

    let leaked = find_leaked_elements(&transcript, &all_elements);
    assert!(
        leaked.is_empty(),
        "server-visible transcript leaked elements: {leaked:?}"
    );
}
