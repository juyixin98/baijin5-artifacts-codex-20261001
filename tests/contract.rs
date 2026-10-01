//! Phase 2 contract: equality/order consistency, representative policy, compatible
//! hashing, version isolation, identity-vs-aggregation-key distinction.

mod common;

use collate_agg::batch::StringBatch;
use collate_agg::collation::{
    canonical_bytes, fnv1a_64, key_hash, normalize, rule_for, sort_key, RepresentativePolicy,
    RuleVersion,
};
use collate_agg::exec::{hash_groups, sort_groups, GroupSet};
use collate_agg::service::{run_comparison_with_id, GroupRequest};
use common::test_state;

fn r1() -> &'static collate_agg::collation::CollationRule {
    rule_for(RuleVersion::V2026R1).unwrap()
}
fn r2() -> &'static collate_agg::collation::CollationRule {
    rule_for(RuleVersion::V2026R2).unwrap()
}

fn batch(values: &[&str]) -> StringBatch {
    StringBatch::try_new("s", values.iter().map(|s| s.to_string()).collect()).unwrap()
}

// --- Equality semantics consistent with sort keys -----------------------------

#[test]
fn accent_and_case_equivalent_under_r1() {
    for a in ["Café", "CAFE", "cafe", "café"] {
        for b in ["Café", "CAFE", "cafe", "café"] {
            let ka = sort_key(r1(), a);
            let kb = sort_key(r1(), b);
            assert!(ka.equivalent(&kb), "R1: `{a}` ~ `{b}` expected");
            assert_eq!(ka.compare(&kb), std::cmp::Ordering::Equal);
        }
    }
}

#[test]
fn case_accent_distinguished_under_r2() {
    // R2 is case- and accent-sensitive: these must NOT collapse.
    let k_cap = sort_key(r2(), "Cafe");
    let k_low = sort_key(r2(), "cafe");
    let k_acc = sort_key(r2(), "café");
    assert!(!k_cap.equivalent(&k_low), "R2 case must differ");
    assert!(!k_low.equivalent(&k_acc), "R2 accent must differ");
    // But ordering must still be total and agree with equality (equal <=> cmp Equal).
    assert_ne!(k_cap.compare(&k_low), std::cmp::Ordering::Equal);
}

#[test]
fn equality_is_consistent_with_ordering_reflexive_and_symmetric() {
    let values = ["a2", "a02", "A2", "a10", "a100", "a1"];
    for v in values {
        let k = sort_key(r1(), v);
        assert_eq!(k.compare(&k), std::cmp::Ordering::Equal);
        assert!(k.equivalent(&k));
    }
    for a in values {
        for b in values {
            let ka = sort_key(r1(), a);
            let kb = sort_key(r1(), b);
            assert_eq!(
                ka.equivalent(&kb),
                ka.compare(&kb) == std::cmp::Ordering::Equal
            );
        }
    }
}

// --- Natural numeric ordering --------------------------------------------------

#[test]
fn numeric_sequences_order_naturally() {
    // Ordering, not just equality: a2 < a10 < a100.
    let k2 = sort_key(r1(), "a2");
    let k10 = sort_key(r1(), "a10");
    let k100 = sort_key(r1(), "a100");
    assert!(k2.compare(&k10) == std::cmp::Ordering::Less);
    assert!(k10.compare(&k100) == std::cmp::Ordering::Less);
    // Leading zeros don't change the class.
    assert!(sort_key(r1(), "a0002").equivalent(&sort_key(r1(), "a2")));
    // Plain lexicographic says a10 < a2 ('1' < '2'); natural says a2 < a10.
    // Compare the SAME operand order so the divergence is visible:
    assert_eq!("a2".cmp("a10"), std::cmp::Ordering::Greater, "lex baseline");
    assert_eq!(k2.compare(&k10), std::cmp::Ordering::Less, "natural");
    assert_ne!(
        k2.compare(&k10),
        "a2".cmp("a10"),
        "natural order must not equal plain lex order"
    );
}

// --- Representative policy keeps an ORIGINAL value ----------------------------

#[test]
fn representative_is_an_original_identity_not_a_key() {
    // NFD spellings are 5 chars (e + combining acute); "Cafe" is 4, so ShortestWins
    // is distinguishable from First/Last and ties don't hide the policy.
    let nfd_upper: String = "CAFE".chars().chain(std::iter::once('\u{0301}')).collect();
    let nfd_lower: String = "cafe".chars().chain(std::iter::once('\u{0301}')).collect();
    let b = batch(&[nfd_upper.as_str(), "Cafe", nfd_lower.as_str()]);

    let first = sort_groups(r1(), &b, RepresentativePolicy::FirstWins);
    let last = sort_groups(r1(), &b, RepresentativePolicy::LastWins);
    let short = sort_groups(r1(), &b, RepresentativePolicy::ShortestWins);

    let f = &first.groups[0];
    let l = &last.groups[0];
    let s = &short.groups[0];
    assert_eq!(first.group_count(), 1);
    assert_eq!(f.representative, nfd_upper);
    assert_eq!(l.representative, nfd_lower);
    assert_eq!(s.representative, "Cafe"); // shortest (4 chars)
                                          // The aggregation key is NOT any of the original identities' literal bytes.
    assert_ne!(f.key_hex, "CAFE");
    assert!(!f.distinct_identities.contains(&f.key_hex));
    // All three distinct original identities are preserved in first-seen order.
    assert_eq!(
        f.distinct_identities,
        vec![nfd_upper.clone(), "Cafe".to_string(), nfd_lower.clone()]
    );
}

// --- Compatible hashing --------------------------------------------------------

#[test]
fn hash_grouping_hashes_equivalent_values_compatibly() {
    let b = batch(&["Café", "cafe", "CAFE", "z9", "Z09"]);
    let hs = hash_groups(r1(), &b, RepresentativePolicy::FirstWins);
    assert_eq!(hs.group_count(), 2);
    // Same key bytes -> same hash, asserted directly.
    for v in ["Café", "cafe", "CAFE"] {
        assert_eq!(
            key_hash(&sort_key(r1(), v)),
            key_hash(&sort_key(r1(), "CAFE"))
        );
    }
    assert_eq!(
        key_hash(&sort_key(r1(), "z9")),
        key_hash(&sort_key(r1(), "Z09"))
    );
    // Hash reported per group matches canonical bytes hashed here independently.
    for g in &hs.groups {
        let bytes = hex_to_bytes(&g.key_hex);
        assert_eq!(fnv1a_64(&bytes), g.hash);
    }
}

#[test]
fn canonical_bytes_distinguish_versions_and_boundaries() {
    let k_r1 = sort_key(r1(), "cafe");
    let k_r2 = sort_key(r2(), "cafe");
    assert_ne!(canonical_bytes(&k_r1), canonical_bytes(&k_r2));
    assert_ne!(key_hash(&k_r1), key_hash(&k_r2));
}

// --- Sort agg vs hash agg agreement -------------------------------------------

#[test]
fn sort_and_hash_executors_produce_identical_partitions() {
    let b = batch(&[
        "item10", "Café", "item2", "cafe", "ITEM02", "item100", "naïve", "NAIVE",
    ]);
    let s = sort_groups(r1(), &b, RepresentativePolicy::FirstWins);
    let h = hash_groups(r1(), &b, RepresentativePolicy::FirstWins);
    assert_eq!(partition(&s), partition(&h));
    assert_eq!(s.group_count(), h.group_count());
    assert_eq!(s.total_rows(), b.len());
    assert_eq!(h.total_rows(), b.len());
}

// --- Identity vs aggregation key ----------------------------------------------

#[test]
fn dedup_count_uses_keys_but_preserves_distinct_identities() {
    let b = batch(&["Café", "cafe", "other", "cafe"]);
    let gs = hash_groups(r1(), &b, RepresentativePolicy::FirstWins);
    assert_eq!(gs.group_count(), 2, "dedup is by key");
    let cafe = gs.group_for_identity("cafe").unwrap();
    assert_eq!(cafe.row_count, 3, "duplicate identical identities counted");
    assert_eq!(
        cafe.distinct_identities,
        vec!["Café", "cafe"],
        "identities retained separately within the class"
    );
}

// --- Version isolation end-to-end via the validator ---------------------------

#[test]
fn mixed_rule_versions_in_one_op_are_rejected() {
    let state = test_state();
    let req = GroupRequest {
        column: "s".into(),
        rule_version: Some("2026R1".into()),
        row_rule_versions: Some(vec!["2026R1".into(), "2026R2".into(), "2026R1".into()]),
        representative_policy: None,
        values: vec!["a".into(), "A".into(), "a".into()],
    };
    let v = run_comparison_with_id(&state, req, "rid-mix".into());
    assert!(v.rejected());
    let failure = v.failure.expect("typed failure");
    let kind = serde_json::to_string(&failure).unwrap();
    assert!(kind.contains("rule_version_mixed"), "got {kind}");
    // Request id and key state travel in diagnostics.
    assert!(v.diagnostics.iter().any(|d| d.request_id == "rid-mix"));
    assert!(v.diagnostics.iter().any(|d| d
        .state
        .iter()
        .any(|(k, val)| k == "distinct_versions" && val.contains("2026R2"))));
}

#[test]
fn unknown_rule_version_is_rejected_not_undetermined_grouping() {
    let state = test_state();
    let req = GroupRequest {
        column: "s".into(),
        rule_version: Some("1999R0".into()),
        row_rule_versions: None,
        representative_policy: None,
        values: vec!["a".into()],
    };
    let v = run_comparison_with_id(&state, req, "rid-unknown".into());
    assert!(v.rejected());
    let kind = serde_json::to_string(&v.failure.unwrap()).unwrap();
    assert!(kind.contains("unknown_rule_version"));
    assert!(kind.contains("1999R0"));
}

#[test]
fn normalization_maps_nfd_to_same_class() {
    // Independently construct NFC and NFD forms here in the test (not from the core).
    let nfc = "Café".to_string();
    let nfd: String = "Cafe".chars().chain(std::iter::once('\u{0301}')).collect();
    assert_ne!(nfc, nfd, "raw identities differ");
    let folded_nfc = normalize(r1(), &nfc);
    let folded_nfd = normalize(r1(), &nfd);
    assert_eq!(folded_nfc, folded_nfd, "normalization converges");
    let b = StringBatch::try_new("s", vec![nfc, nfd]).unwrap();
    let gs = sort_groups(r1(), &b, RepresentativePolicy::FirstWins);
    assert_eq!(gs.group_count(), 1);
}

// --- helpers -------------------------------------------------------------------

fn partition(gs: &GroupSet) -> std::collections::BTreeSet<Vec<usize>> {
    gs.groups
        .iter()
        .map(|g| {
            let mut rows = g.rows.clone();
            rows.sort_unstable();
            rows
        })
        .collect()
}

fn hex_to_bytes(hex: &str) -> Vec<u8> {
    (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
        .collect()
}
