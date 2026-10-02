//! Wire (de)serialization contract tests: the JSON spelling of types and
//! enums must match the documented examples, not just Rust identifiers.

use recursive_cte_backend::plan::{
    ColumnType, CycleMode, IncompleteReason, RunStatus, SetQuantifier, TraversalOrder,
};
use serde_json::json;

#[test]
fn column_type_wire_spelling_matches_documentation() {
    for (ty, wire) in [
        (ColumnType::Int64, "int64"),
        (ColumnType::Utf8, "utf8"),
        (ColumnType::Bool, "bool"),
        (ColumnType::ListInt64, "list<int64>"),
        (ColumnType::ListUtf8, "list<utf8>"),
    ] {
        assert_eq!(serde_json::to_value(ty).unwrap(), json!(wire));
        let parsed: ColumnType = serde_json::from_value(json!(wire)).unwrap();
        assert_eq!(parsed, ty);
    }
}

#[test]
fn enum_wire_spelling_is_stable() {
    assert_eq!(
        serde_json::to_value(SetQuantifier::UnionAll).unwrap(),
        json!("union_all")
    );
    assert_eq!(
        serde_json::to_value(TraversalOrder::Dfs).unwrap(),
        json!("dfs")
    );
    assert_eq!(
        serde_json::to_value(CycleMode::Mark).unwrap(),
        json!("mark")
    );
    assert_eq!(
        serde_json::to_value(RunStatus::Incomplete).unwrap(),
        json!("incomplete")
    );
    assert_eq!(
        serde_json::to_value(IncompleteReason::MaxRows).unwrap(),
        json!("max_rows")
    );
}

#[test]
fn example_request_files_parse_with_documented_type_spelling() {
    for path in [
        "examples/self_loop.json",
        "examples/diamond_union_all.json",
        "examples/two_cycle_text.json",
    ] {
        let raw = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("read {path}: {e}"));
        let req: recursive_cte_backend::RecursiveRequest =
            serde_json::from_str(&raw).unwrap_or_else(|e| panic!("parse {path}: {e}"));
        recursive_cte_backend::plan::validate_request(&req)
            .unwrap_or_else(|e| panic!("validate {path}: {e}"));
    }
}

#[test]
fn unknown_type_variant_is_rejected_not_coerced() {
    let err = serde_json::from_value::<ColumnType>(json!("list<int32>")).unwrap_err();
    assert!(err.to_string().contains("unknown variant"));
}
