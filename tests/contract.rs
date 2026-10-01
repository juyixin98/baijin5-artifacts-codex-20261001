//! Phase 2+3 contract tests through the shared `verify` entry point and both
//! executors. Expected answers come from `common` (independent oracle +
//! literals), never from the implementation under test.

mod common;

use std::collections::HashSet;
use std::sync::Arc;

use collation_agg_contract::operators::{dedup, hash_group, sort_group};
use collation_agg_contract::{
    batch::{InputBatch, Row},
    collation::{Collator, Rule},
    state::AppState,
    verify, ApiRow, Category, Settings, VerifyRequest,
};

use common::{
    oracle_group_count_v1, synth_strings, ACCENT_EXPECTED_DISTINCT, ACCENT_EXPECTED_GROUPS,
    NORMALIZATION_EXPECTED_GROUPS, NUMERIC_EXPECTED_DISTINCT, NUMERIC_EXPECTED_GROUPS,
};

fn rows(fix: &[common::FixRow]) -> Vec<Row> {
    fix.iter()
        .map(|(id, v)| Row {
            record_id: (*id).to_string(),
            value: v.map(|s| s.to_string()),
        })
        .collect()
}

fn api_rows(fix: &[common::FixRow]) -> Vec<ApiRow> {
    fix.iter()
        .map(|(id, v)| ApiRow {
            record_id: (*id).to_string(),
            value: v.map(|s| s.to_string()),
        })
        .collect()
}

fn state() -> Arc<AppState> {
    Arc::new(AppState::new(Settings::default()))
}

// --------------------------------------------------------------------------
// Phase 2: equality semantics == sort key, representative retained.
// --------------------------------------------------------------------------

#[test]
fn equality_and_sort_key_agree_accent_case() {
    let b = InputBatch::from_rows(&rows(&common::accent_rows())).unwrap();
    let c = Collator::new(Rule::v1());
    let out = sort_group(&b, &c).unwrap();

    assert_eq!(out.group_count(), ACCENT_EXPECTED_GROUPS);
    // NULL sorts last; real representatives precede it.
    assert_eq!(
        out.distinct_values(),
        ACCENT_EXPECTED_DISTINCT
            .iter()
            .map(|s| s.to_string())
            .collect::<Vec<_>>()
    );
    // Representatives are ORIGINAL values, not normalized keys.
    let cafe = out
        .groups
        .iter()
        .find(|g| !g.is_null && g.count == 3)
        .unwrap();
    assert_eq!(cafe.representative, "Café");
    assert_eq!(cafe.representative_record_id, "r1");
    assert_eq!(cafe.member_record_ids, vec!["r1", "r2", "r3"]);
}

#[test]
fn representative_is_first_input_value_not_the_key() {
    // Even when a non-canonical spelling appears first, it is retained.
    let rows = vec![
        Row::new("a", "CAFE"),
        Row::new("b", "café"),
        Row::new("c", "cafe\u{0301}"),
    ];
    let b = InputBatch::from_rows(&rows).unwrap();
    let out = sort_group(&b, &Collator::new(Rule::v1())).unwrap();
    assert_eq!(out.group_count(), 1);
    assert_eq!(out.groups[0].representative, "CAFE");
}

// --------------------------------------------------------------------------
// Phase 2: hash grouping is compatible with equivalence.
// --------------------------------------------------------------------------

#[test]
fn hash_grouping_matches_sort_and_oracle() {
    let b = InputBatch::from_rows(&rows(&common::accent_rows())).unwrap();
    let c = Collator::new(Rule::v1());
    let s = sort_group(&b, &c).unwrap();
    let h = hash_group(&b, &c).unwrap();

    // Byte-for-byte agreement between the two strategies.
    assert_eq!(s, h);
    // And agreement with the independent oracle's partition size.
    assert_eq!(
        s.group_count(),
        oracle_group_count_v1(&common::accent_rows())
    );
}

#[test]
fn hash_compatible_on_numeric_and_normalization() {
    for fix in [common::numeric_rows(), common::normalization_rows()] {
        let b = InputBatch::from_rows(&rows(&fix)).unwrap();
        let c = Collator::new(Rule::v1());
        assert_eq!(sort_group(&b, &c).unwrap(), hash_group(&b, &c).unwrap());
        assert_eq!(
            sort_group(&b, &c).unwrap().group_count(),
            oracle_group_count_v1(&fix)
        );
    }
    let nb = InputBatch::from_rows(&rows(&common::numeric_rows())).unwrap();
    let out = sort_group(&nb, &Collator::new(Rule::v1())).unwrap();
    assert_eq!(out.group_count(), NUMERIC_EXPECTED_GROUPS);
    assert_eq!(
        out.distinct_values(),
        NUMERIC_EXPECTED_DISTINCT
            .iter()
            .map(|s| s.to_string())
            .collect::<Vec<_>>()
    );
    let zb = InputBatch::from_rows(&rows(&common::normalization_rows())).unwrap();
    assert_eq!(
        sort_group(&zb, &Collator::new(Rule::v1()))
            .unwrap()
            .group_count(),
        NORMALIZATION_EXPECTED_GROUPS
    );
}

#[test]
fn natural_number_ordering_file2_before_file10() {
    let rows = vec![Row::new("x", "file10"), Row::new("y", "file2")];
    let b = InputBatch::from_rows(&rows).unwrap();
    let out = sort_group(&b, &Collator::new(Rule::v1())).unwrap();
    assert_eq!(out.groups[0].representative, "file2");
    assert_eq!(out.groups[1].representative, "file10");
}

// --------------------------------------------------------------------------
// Phase 2: different rule versions never mix.
// --------------------------------------------------------------------------

#[test]
fn keys_from_different_rule_versions_are_never_equal() {
    let c1 = Collator::new(Rule::v1());
    let c2 = Collator::new(Rule::v2());
    // Even for the very same raw string the key fingerprints differ because
    // the rule version is part of the key.
    assert_ne!(c1.key_fingerprint("ABC"), c2.key_fingerprint("ABC"));
    // Under v2 the case/accent variants are distinct classes.
    let rows = vec![
        Row::new("a", "Café"),
        Row::new("b", "café"),
        Row::new("c", "cafe"),
    ];
    let b = InputBatch::from_rows(&rows).unwrap();
    assert_eq!(hash_group(&b, &c2).unwrap().group_count(), 3);
    assert_eq!(hash_group(&b, &c1).unwrap().group_count(), 1);
}

#[test]
fn session_rejects_a_rule_version_switch() {
    let s = state();
    let first = VerifyRequest {
        request_id: Some("t1".into()),
        rule_version: 1,
        rows: api_rows(&common::accent_rows()),
        session_id: Some("sess".into()),
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    let r1 = verify(&s, first).unwrap();
    assert_eq!(r1.category, Category::Accepted);
    assert_eq!(r1.session.as_ref().unwrap().batches, 1);

    let switched = VerifyRequest {
        request_id: Some("t2".into()),
        rule_version: 2,
        rows: vec![ApiRow {
            record_id: "x".into(),
            value: Some("Café".into()),
        }],
        session_id: Some("sess".into()),
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    let r2 = verify(&s, switched).unwrap();
    assert_eq!(r2.category, Category::RejectRuleVersionMismatch);
    assert_eq!(r2.status, "rejected");
    // The rejected batch must not have been folded in: still 1 batch.
    assert!(r2.session.is_none());
    let view = s.sessions.get("sess").unwrap();
    assert_eq!(view.batches, 1);
    assert_eq!(view.rule_version, 1);
}

// --------------------------------------------------------------------------
// Phase 2: original identity is distinct from aggregation key.
// --------------------------------------------------------------------------

#[test]
fn record_identity_survives_collapse() {
    let ids: HashSet<String> = ["r1", "r2", "r3"].iter().map(|s| s.to_string()).collect();
    let b = InputBatch::from_rows(&rows(&common::accent_rows()[..3])).unwrap();
    let out = hash_group(&b, &Collator::new(Rule::v1())).unwrap();
    let g = &out.groups[0];
    let members: HashSet<String> = g.member_record_ids.iter().cloned().collect();
    assert_eq!(members, ids);
    // One aggregation key, three distinct identities.
    assert_eq!(g.count, 3);
    assert_eq!(g.member_record_ids.len(), 3);
}

#[test]
fn dedup_preserves_original_value_and_input_order() {
    let rows = vec![
        Row::new("d1", "CAFE"),
        Row::new("d2", "café"),
        Row::new("d3", "tea"),
    ];
    let out = dedup(
        &InputBatch::from_rows(&rows).unwrap(),
        &Collator::new(Rule::v1()),
    )
    .unwrap();
    assert_eq!(out.len(), 2);
    assert_eq!(out[0].record_id, "d1");
    assert_eq!(out[0].value.as_deref(), Some("CAFE"));
    assert_eq!(out[1].value.as_deref(), Some("tea"));
}

// --------------------------------------------------------------------------
// Phase 3: verify entry point end-to-end incl. concrete failure categories.
// --------------------------------------------------------------------------

#[test]
fn verify_accepts_with_independent_expectations() {
    let s = state();
    let req = VerifyRequest {
        request_id: Some("acc".into()),
        rule_version: 1,
        rows: api_rows(&common::accent_rows()),
        session_id: None,
        expect_group_count: Some(ACCENT_EXPECTED_GROUPS),
        expect_distinct: Some(
            ACCENT_EXPECTED_DISTINCT
                .iter()
                .map(|s| s.to_string())
                .collect(),
        ),
        sensitive: false,
    };
    let r = verify(&s, req).unwrap();
    assert_eq!(r.category, Category::Accepted);
    assert_eq!(r.group_count, Some(ACCENT_EXPECTED_GROUPS));
    assert_eq!(r.sort_groups, r.hash_groups);
}

#[test]
fn verify_rejects_wrong_independent_count_with_named_category() {
    let s = state();
    let req = VerifyRequest {
        request_id: Some("bad".into()),
        rule_version: 1,
        rows: api_rows(&common::accent_rows()),
        session_id: None,
        expect_group_count: Some(99), // deliberately wrong
        expect_distinct: None,
        sensitive: false,
    };
    let r = verify(&s, req).unwrap();
    assert_eq!(r.category, Category::RejectExpectedCount);
    assert!(r.detail.contains("expected 99 groups"));
}

#[test]
fn unknown_rule_version_is_rejected_not_defaulted() {
    let s = state();
    let req = VerifyRequest {
        request_id: Some("unk".into()),
        rule_version: 7,
        rows: vec![ApiRow {
            record_id: "x".into(),
            value: Some("a".into()),
        }],
        session_id: None,
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    let r = verify(&s, req).unwrap();
    assert_eq!(r.category, Category::RejectUnknownRule);
    assert_eq!(r.status, "rejected");
    assert!(r.group_count.is_none());
}

#[test]
fn duplicate_and_empty_record_ids_are_rejected() {
    let s = state();
    let dup = VerifyRequest {
        request_id: None,
        rule_version: 1,
        rows: vec![
            ApiRow {
                record_id: "k".into(),
                value: Some("a".into()),
            },
            ApiRow {
                record_id: "k".into(),
                value: Some("b".into()),
            },
        ],
        session_id: None,
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    assert_eq!(
        verify(&s, dup).unwrap().category,
        Category::RejectDuplicateRecordId
    );

    let empty = VerifyRequest {
        request_id: None,
        rule_version: 1,
        rows: vec![ApiRow {
            record_id: String::new(),
            value: Some("a".into()),
        }],
        session_id: None,
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    assert_eq!(
        verify(&s, empty).unwrap().category,
        Category::RejectEmptyRecordId
    );
}

#[test]
fn numeric_overflow_is_undetermined_not_accepted() {
    let s = state();
    let huge = "9".repeat(30); // wider than u64
    let req = VerifyRequest {
        request_id: Some("ovf".into()),
        rule_version: 1,
        rows: vec![
            ApiRow {
                record_id: "big".into(),
                value: Some(huge),
            },
            ApiRow {
                record_id: "big2".into(),
                value: Some(format!("{}0", "9".repeat(30))),
            },
        ],
        session_id: None,
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    let r = verify(&s, req).unwrap();
    assert_eq!(r.category, Category::UndeterminedNumericOverflow);
    assert_eq!(r.status, "undetermined");
    assert_eq!(
        r.overflow_record_ids,
        vec!["big".to_string(), "big2".to_string()]
    );
}

// --------------------------------------------------------------------------
// Property-style check against the independent oracle on synthetic input.
// --------------------------------------------------------------------------

#[test]
fn property_group_count_matches_oracle_on_synthetic_input() {
    for seed in 1..=50u64 {
        let strs = synth_strings(seed, 40);
        // Owned typed rows fed to the implementation under test.
        let owned: Vec<Row> = strs
            .iter()
            .enumerate()
            .map(|(i, s)| Row::new(format!("p{i}"), s.clone()))
            .collect();
        // Borrowed pairs fed to the independent oracle (separate construction).
        let pairs: Vec<(String, Option<String>)> = owned
            .iter()
            .map(|r| (r.record_id.clone(), r.value.clone()))
            .collect();

        let b = InputBatch::from_rows(&owned).unwrap();
        let c = Collator::new(Rule::v1());
        let s = sort_group(&b, &c).unwrap();
        let h = hash_group(&b, &c).unwrap();
        assert_eq!(s, h, "executor disagreement for seed {seed}");
        assert_eq!(
            s.group_count(),
            oracle_group_count_v1(&pairs),
            "oracle count mismatch for seed {seed}"
        );
    }
}
