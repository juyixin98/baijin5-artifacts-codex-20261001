//! Shared test fixtures and helpers.
//!
//! Expected answers here are **hand-derived**, not produced by the engine
//! under test: each test names the precise surviving rows / error category a
//! human computes from the fixture definition below. The crosscheck in the
//! pipeline additionally proves the two engines agree, but a bug shared by
//! both engines would still fail the hand-derived assertions.

// Helpers are shared across separate integration-test binaries; not every
// binary uses every helper, so unused-in-one-binary warnings are expected.
#![allow(dead_code)]

use serde_json::{json, Value};

pub fn request_id() -> &'static str {
    "test-fixed-request-id"
}

/// The shared fixture document.
///
/// Outer `orders` (alias `o`):
///
/// | o_id | cust | want |
/// |------|------|------|
/// | 1    | 10   | 1    |
/// | 2    | 20   | 2    |
/// | 3    | 10   | 1    |  duplicate correlation key (cust=10)
/// | 4    | NULL | 1    |  NULL correlation key
/// | 5    | 30   | 5    |  no inner rows
/// | 6    | 40   | 5    |  only present in the multi-row scalar fixture
/// | 7    | 10   | 999  |  duplicate key, value exercises IN NULL trap
///
/// Inner `lines` (alias `l`):
///
/// | l_id | cust | amt  | code | tag |
/// |------|------|------|------|-----|
/// | 101  | 10   | 5    | 1    | "a" |
/// | 102  | 10   | 7    | NULL | "b" |  cust 10 group: 2 rows, NULL code
/// | 103  | 20   | NULL | 2    | "c" |  only NULL amt in group
/// | 104  | NULL | 9    | 1    | "d" |  inner NULL corr key: never matches
///
/// `empty_lines` has the same schema but zero rows.
///
/// `scalar_src` (alias s) is only for the scalar cardinality counterexample:
/// (cust=10 -> one code 9), (cust=40 -> codes 5 and 6, two rows).
pub fn fixtures() -> Value {
    json!({
        "relations": [
            {
                "name": "orders",
                "columns": [
                    {"name": "o_id", "type": "int"},
                    {"name": "cust", "type": "int"},
                    {"name": "want", "type": "int"}
                ],
                "rows": [
                    [1, 10, 1],
                    [2, 20, 2],
                    [3, 10, 1],
                    [4, null, 1],
                    [5, 30, 5],
                    [6, 40, 5],
                    [7, 10, 999]
                ]
            },
            {
                "name": "lines",
                "columns": [
                    {"name": "l_id", "type": "int"},
                    {"name": "cust", "type": "int"},
                    {"name": "amt", "type": "int"},
                    {"name": "code", "type": "int"},
                    {"name": "tag", "type": "str"}
                ],
                "rows": [
                    [101, 10, 5, 1, "a"],
                    [102, 10, 7, null, "b"],
                    [103, 20, null, 2, "c"],
                    [104, null, 9, 1, "d"]
                ]
            },
            {
                "name": "empty_lines",
                "columns": [
                    {"name": "l_id", "type": "int"},
                    {"name": "cust", "type": "int"},
                    {"name": "amt", "type": "int"},
                    {"name": "code", "type": "int"},
                    {"name": "tag", "type": "str"}
                ],
                "rows": []
            },
            {
                "name": "scalar_src",
                "columns": [
                    {"name": "cust", "type": "int"},
                    {"name": "code", "type": "int"}
                ],
                "rows": [
                    [10, 9],
                    [40, 5],
                    [40, 6]
                ]
            }
        ]
    })
}

pub fn col(name: &str) -> Value {
    json!({"kind": "column", "column": name})
}

pub fn qcol(table: &str, name: &str) -> Value {
    json!({"kind": "column", "table": table, "column": name})
}

pub fn int_lit(v: i64) -> Value {
    json!({"kind": "literal", "type": "int", "value": v.to_string()})
}

pub fn corr(outer_table: &str, outer_col: &str, inner_table: &str, inner_col: &str) -> Value {
    json!({
        "kind": "correlated",
        "left": {"table": outer_table, "column": outer_col},
        "right": {"table": inner_table, "column": inner_col}
    })
}

pub fn sub(relation: &str, alias: &str, terms: Value, extra: Value) -> Value {
    let mut m = serde_json::Map::new();
    m.insert("from".into(), json!({"relation": relation, "alias": alias}));
    m.insert("where_terms".into(), terms);
    if let Value::Object(map) = extra {
        for (k, v) in map {
            m.insert(k, v);
        }
    }
    Value::Object(m)
}

pub fn exists_term(relation: &str, alias: &str, negated: bool) -> Value {
    let terms = json!([corr("o", "cust", alias, "cust")]);
    json!({
        "kind": "exists",
        "negated": negated,
        "sub": sub(relation, alias, terms, json!({}))
    })
}

pub fn agg_term(
    relation: &str,
    alias: &str,
    func: &str,
    agg_col: &str,
    op: &str,
    outer: Value,
) -> Value {
    let terms = json!([corr("o", "cust", alias, "cust")]);
    let extra = json!({"aggregate": {"func": func, "column": qcol(alias, agg_col)}});
    json!({
        "kind": "scalar_sub",
        "outer": outer,
        "op": op,
        "sub": sub(relation, alias, terms, extra)
    })
}

pub fn in_term(relation: &str, alias: &str, project: &str, outer: Value, negated: bool) -> Value {
    let terms = json!([corr("o", "cust", alias, "cust")]);
    let extra = json!({"project": qcol(alias, project)});
    json!({
        "kind": "in_sub",
        "outer": outer,
        "negated": negated,
        "sub": sub(relation, alias, terms, extra)
    })
}

pub fn query(where_terms: Value) -> Value {
    json!({
        "select": [{"column": "o_id"}],
        "from": {"relation": "orders", "alias": "o"},
        "where_terms": where_terms
    })
}

pub fn request_with_query(q: Value) -> Value {
    json!({"query": q, "fixtures": fixtures()})
}

pub fn request(where_terms: Value) -> Value {
    request_with_query(query(where_terms))
}

/// First column of a successful outcome as integer rows (NULL -> None).
pub fn id_rows(outcome: &Value) -> Vec<Option<i64>> {
    outcome["result"]["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            let v = &r[0];
            if v.is_null() {
                None
            } else {
                Some(v.as_i64().unwrap())
            }
        })
        .collect()
}
