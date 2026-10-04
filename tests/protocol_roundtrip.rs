//! In-process protocol roundtrip tests. Expected intersections are computed
//! by the independent reference (`verify::plaintext_intersection`) and by
//! hardcoded literals — never by the protocol code under test.

mod common;

use psi_dh::crypto;
use psi_dh::protocol::{normalize_set, PartyA, PartyB};
use psi_dh::verify::plaintext_intersection;

fn run_protocol(
    session_id: [u8; 32],
    a_elements: &[Vec<u8>],
    b_elements: &[Vec<u8>],
) -> Vec<Vec<u8>> {
    let (party_a, _) = PartyA::new(session_id, a_elements);
    let a_blinded = crypto::decode_points(&party_a.blinded_message()).expect("A points valid");
    let b_response = PartyB::respond(&session_id, b_elements, &a_blinded);
    let b_blinded = crypto::decode_points(&b_response.b_blinded).expect("B points valid");
    let a_doubly = crypto::decode_points(&b_response.a_doubly).expect("doubly points valid");
    party_a
        .compute_intersection(&b_blinded, &a_doubly)
        .expect("intersection")
}

#[test]
fn synthetic_overlap_matches_plaintext_reference() {
    let session = crypto::generate_session_id();
    let a = common::elements("item", 0..50);
    let b = common::elements("item", 25..75);

    let got = run_protocol(session, &a, &b);
    let reference = plaintext_intersection(&a, &b);

    assert_eq!(got, reference);
    // Hardcoded expectation, independent of any implementation:
    assert_eq!(got, common::elements("item", 25..50));
}

#[test]
fn disjoint_sets_yield_empty_intersection() {
    let session = crypto::generate_session_id();
    let a = common::elements("alpha", 0..20);
    let b = common::elements("beta", 0..20);

    let got = run_protocol(session, &a, &b);

    assert!(got.is_empty());
    assert_eq!(got, plaintext_intersection(&a, &b));
}

#[test]
fn empty_inputs_yield_empty_intersection() {
    let session = crypto::generate_session_id();
    let a: Vec<Vec<u8>> = vec![];
    let b = common::elements("item", 0..5);
    assert!(run_protocol(session, &a, &b).is_empty());
    assert!(run_protocol(session, &b, &a).is_empty());
    assert!(run_protocol(session, &a, &a).is_empty());
}

#[test]
fn identical_sets_yield_full_set() {
    let session = crypto::generate_session_id();
    let a = common::elements("same", 0..30);
    let got = run_protocol(session, &a, &a);
    assert_eq!(got, a);
}

#[test]
fn session_isolation_changes_blinded_points() {
    // Same element, two sessions: the blinded points must differ, because the
    // session id is mixed into hash-to-group and fresh scalars are drawn.
    let e = vec![b"same-element".to_vec()];
    let (a1, _) = PartyA::new([1u8; 32], &e);
    let (a2, _) = PartyA::new([2u8; 32], &e);
    assert_ne!(a1.blinded_message(), a2.blinded_message());

    // Same session id, fresh scalars: still different (fresh randomness).
    let (b1, _) = PartyA::new([3u8; 32], &e);
    let (b2, _) = PartyA::new([3u8; 32], &e);
    assert_ne!(b1.blinded_message(), b2.blinded_message());
}

#[test]
fn normalize_set_collapses_duplicates() {
    let input = vec![
        b"b".to_vec(),
        b"a".to_vec(),
        b"b".to_vec(),
        b"a".to_vec(),
        b"c".to_vec(),
    ];
    let (normalized, report) = normalize_set(&input);
    assert_eq!(normalized, vec![b"a".to_vec(), b"b".to_vec(), b"c".to_vec()]);
    assert_eq!(report.input_len, 5);
    assert_eq!(report.unique_len, 3);
    assert_eq!(report.duplicates_removed(), 2);
}
