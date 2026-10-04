//! Duplicate-element and set-semantics tests, including over the wire.

mod common;

use psi_dh::crypto;
use psi_dh::protocol::{PartyA, PartyB};
use psi_dh::verify::plaintext_intersection;

#[test]
fn duplicates_in_inputs_do_not_change_output() {
    let session = crypto::generate_session_id();
    let mut a = common::elements("item", 0..30);
    let mut b = common::elements("item", 10..40);
    // Sprinkle duplicates into both inputs.
    a.extend(common::elements("item", 0..10));
    a.extend(common::elements("item", 5..8));
    b.extend(common::elements("item", 20..25));

    let (party_a, report_a) = PartyA::new(session, &a);
    assert_eq!(report_a.duplicates_removed(), 13);
    assert_eq!(party_a.element_count(), 30);

    let a_blinded = crypto::decode_points(&party_a.blinded_message()).unwrap();
    let b_response = PartyB::respond(&session, &b, &a_blinded);
    assert_eq!(b_response.dedupe.duplicates_removed(), 5);
    assert_eq!(b_response.b_blinded.len(), 30);

    let b_blinded = crypto::decode_points(&b_response.b_blinded).unwrap();
    let a_doubly = crypto::decode_points(&b_response.a_doubly).unwrap();
    let got = party_a.compute_intersection(&b_blinded, &a_doubly).unwrap();

    // Set semantics: the result equals the plain intersection of the
    // deduplicated inputs — no element appears twice.
    let clean_a = common::elements("item", 0..30);
    let clean_b = common::elements("item", 10..40);
    assert_eq!(got, plaintext_intersection(&clean_a, &clean_b));
    assert_eq!(got, common::elements("item", 10..30));
}

#[test]
fn wire_format_contains_no_duplicate_points() {
    let session = crypto::generate_session_id();
    let mut a = common::elements("dup", 0..10);
    a.extend(common::elements("dup", 0..10)); // every element twice
    let (party_a, _) = PartyA::new(session, &a);
    let msg = party_a.blinded_message();
    let unique: std::collections::BTreeSet<_> = msg.iter().collect();
    assert_eq!(msg.len(), unique.len(), "blinded message must be a set");
    assert_eq!(msg.len(), 10);
}
