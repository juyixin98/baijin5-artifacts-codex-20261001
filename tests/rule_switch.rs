//! Rule-switching rejection: unknown versions, per-row mixed versions, and R1/R2
//! producing deliberately different group counts (so a switch is observable, and a
//! switch mid-operation is rejected with a typed failure category).

mod common;

use collate_agg::diag::FailureCategory;
use collate_agg::service::{run_comparison_with_id, validate_no_version_mix, GroupRequest};
use collate_agg::{collation::RuleVersion, state::AppConfig, AppState};
use common::{test_state, Fixture};

fn req(rule: &str, rows: &[&str], per_row: Option<Vec<&str>>) -> GroupRequest {
    GroupRequest {
        column: "label".into(),
        rule_version: Some(rule.into()),
        row_rule_versions: per_row.map(|v| v.into_iter().map(String::from).collect()),
        representative_policy: None,
        values: rows.iter().map(|s| s.to_string()).collect(),
    }
}

#[test]
fn rejects_mixing_two_known_versions_with_conflict_409() {
    let state = test_state();
    let v = run_comparison_with_id(
        &state,
        req("2026R1", &["Cafe", "cafe"], Some(vec!["2026R1", "2026R2"])),
        "rid-switch".into(),
    );
    assert!(v.rejected());
    match v.failure {
        Some(FailureCategory::RuleVersionMixed { versions }) => {
            assert_eq!(versions, vec!["2026R1".to_string(), "2026R2".to_string()]);
        }
        other => panic!("expected RuleVersionMixed, got {other:?}"),
    }
    // No grouping happened and no results are surfaced.
    assert!(v.comparison.is_none());
    assert!(v.sort_result.is_none());
    // The rejection diagnostic records the key state.
    assert!(v.diagnostics.iter().any(|d| d.stage == "version_mix"));
}

#[test]
fn accepts_when_all_rows_tagged_same_version() {
    let state = test_state();
    let v = run_comparison_with_id(
        &state,
        req("2026R2", &["Cafe", "cafe"], Some(vec!["2026R2", "2026R2"])),
        "rid-ok".into(),
    );
    assert!(v.accepted(), "diags {:?}", v.diagnostics);
    // Under R2 these stay distinct (2 groups).
    assert_eq!(v.comparison.unwrap().sort_group_count, 2);
}

#[test]
fn unknown_version_tag_in_rows_is_classified_as_unknown_rule_version() {
    let err = validate_no_version_mix(
        RuleVersion::V2026R1,
        &["2026R1".to_string(), "future-R3".to_string()],
    )
    .expect_err("must error");
    match err {
        FailureCategory::UnknownRuleVersion { supplied } => assert_eq!(supplied, "future-R3"),
        other => panic!("expected UnknownRuleVersion, got {other:?}"),
    }
}

#[test]
fn rule_version_change_changes_group_count() {
    // The same data groups differently under R1 vs R2 — proving versions are real.
    let fx = Fixture::load();
    let state = test_state();

    let mut r1_req = fx.request();
    r1_req.rule_version = Some("2026R1".into());
    let v1 = run_comparison_with_id(&state, r1_req, "rid-r1".into());

    let mut r2_req = fx.request();
    r2_req.rule_version = Some("2026R2".into());
    let v2 = run_comparison_with_id(&state, r2_req, "rid-r2".into());

    let c1 = v1.comparison.clone().unwrap();
    let c2 = v2.comparison.clone().unwrap();
    assert!(v1.accepted() && v2.accepted());
    assert_eq!(c1.sort_group_count, 8);
    assert!(
        c2.sort_group_count > c1.sort_group_count,
        "R2 stricter -> more groups"
    );
    assert_ne!(
        v1.sort_result.clone().unwrap().groups[0].key_hex,
        v2.sort_result
            .unwrap()
            .groups
            .iter()
            .find(|g| g.representative == "Café")
            .map(|g| g.key_hex.clone())
            .unwrap(),
        "version byte makes the keys differ"
    );
}

#[test]
fn unknown_version_request_is_rejected_400() {
    let state = test_state();
    let v = run_comparison_with_id(&state, req("3026R9", &["a"], None), "rid-bad-ver".into());
    assert!(v.rejected());
    match v.failure {
        Some(FailureCategory::UnknownRuleVersion { supplied }) => assert_eq!(supplied, "3026R9"),
        other => panic!("expected UnknownRuleVersion, got {other:?}"),
    }
}

#[test]
fn empty_column_rejected_and_large_batch_guarded() {
    let state = test_state();
    let v = run_comparison_with_id(
        &state,
        GroupRequest {
            column: String::new(),
            rule_version: None,
            row_rule_versions: None,
            representative_policy: None,
            values: vec!["a".into()],
        },
        "rid-empty".into(),
    );
    assert!(matches!(v.failure, Some(FailureCategory::EmptyColumn)));

    let small = AppState::new(AppConfig {
        max_rows: 2,
        ..AppConfig::default()
    });
    let v = run_comparison_with_id(
        &small,
        req("2026R1", &["a", "b", "c"], None),
        "rid-toobig".into(),
    );
    assert!(matches!(
        v.failure,
        Some(FailureCategory::InvalidRow { .. })
    ));
}
