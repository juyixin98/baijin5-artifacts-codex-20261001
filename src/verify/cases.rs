//! Built-in differential scenarios.
//!
//! Coverage is chosen to hit every boundary called out in the task:
//! * NULL crossing NULL through AND/OR/NOT (Kleene tables),
//! * a column that is NULL on every row (all-unknown),
//! * a 67-row universe (non-machine-word-sized tail) with deleted rows in the
//!   tail,
//! * versioned deletes and as-of time travel,
//! * explicit failure-category expectations (not just "the API replied").

use serde_json::json;

use crate::verify::runner::Case;

/// Cases over the small mixed-NULL `people` fixture.
pub fn people_cases() -> Vec<Case> {
    vec![
        // Plain comparison: known rows T/F, NULL row U.
        Case::ok(
            "age-eq-30",
            json!({"op":"cmp","column":"age","cmp":"=","value":30}),
            None,
        ),
        // NOT must keep NULL rows UNKNOWN, not flip them to FALSE.
        Case::ok(
            "not-age-eq-30",
            json!({"op":"not","arg":{"op":"cmp","column":"age","cmp":"=","value":30}}),
            None,
        ),
        // NULL AND FALSE = FALSE, NULL AND TRUE = UNKNOWN: crossed nulls.
        Case::ok(
            "age-null-cross-active",
            json!({"op":"and","args":[
                {"op":"cmp","column":"age","cmp":">=","value":18},
                {"op":"cmp","column":"active","cmp":"=","value":true}
            ]}),
            None,
        ),
        // NULL OR TRUE = TRUE; FALSE OR UNKNOWN = UNKNOWN.
        Case::ok(
            "age-or-name-null",
            json!({"op":"or","args":[
                {"op":"cmp","column":"age","cmp":">=","value":35},
                {"op":"is_null","column":"name"}
            ]}),
            None,
        ),
        // IS NULL never yields UNKNOWN itself.
        Case::ok("age-is-null", json!({"op":"is_null","column":"age"}), None),
        Case::ok(
            "name-is-not-null",
            json!({"op":"is_not_null","column":"name"}),
            None,
        ),
        // Nested NOT twice must be identity on all three values.
        Case::ok(
            "double-not-age",
            json!({"op":"not","arg":{"op":"not","arg":
                {"op":"cmp","column":"age","cmp":"<","value":35}}}),
            None,
        ),
        // All-unknown column: every live row UNKNOWN, none selected.
        Case::ok(
            "all-unknown-note",
            json!({"op":"cmp","column":"note","cmp":"=","value":"anything"}),
            None,
        ),
        // All-unknown then IS NULL -> all TRUE (UNKNOWN collapsed).
        Case::ok(
            "all-unknown-note-is-null",
            json!({"op":"is_null","column":"note"}),
            None,
        ),
        // Combined AND over three predicates mixing NULL columns.
        Case::ok(
            "triple-and-mixed-nulls",
            json!({"op":"and","args":[
                {"op":"cmp","column":"active","cmp":"=","value":true},
                {"op":"cmp","column":"age","cmp":">=","value":18},
                {"op":"is_not_null","column":"name"}
            ]}),
            None,
        ),
        // Time travel: at v1 the row deleted at v2 must be live again and its
        // NULL age must evaluate UNKNOWN, proving version handling + 3VL.
        Case::ok(
            "asof-v1-before-delete",
            json!({"op":"is_null","column":"age"}),
            Some(1),
        ),
        Case::ok(
            "asof-v2-after-delete",
            json!({"op":"is_null","column":"age"}),
            Some(2),
        ),
        // ---- explicit failure categories ----------------------------------
        Case::error(
            "null-literal-must-use-is-null",
            json!({"op":"cmp","column":"age","cmp":"=","value":null}),
            None,
            "invalid_query",
        ),
        Case::error(
            "unknown-column",
            json!({"op":"cmp","column":"nope","cmp":"=","value":1}),
            None,
            "unknown_column",
        ),
        Case::error(
            "type-mismatch-int-vs-string",
            json!({"op":"cmp","column":"age","cmp":"=","value":"oops"}),
            None,
            "type_error",
        ),
        Case::error(
            "type-mismatch-bool-vs-number",
            json!({"op":"cmp","column":"active","cmp":"=","value":1}),
            None,
            "type_error",
        ),
        Case::error(
            "unknown-operator",
            json!({"op":"cmp","column":"age","cmp":"~~","value":1}),
            None,
            "invalid_query",
        ),
        Case::error(
            "empty-and",
            json!({"op":"and","args":[]}),
            None,
            "invalid_query",
        ),
        Case::error(
            "as-of-ahead-of-head",
            json!({"op":"is_null","column":"age"}),
            Some(99),
            "invalid_query",
        ),
    ]
}

/// Cases over the 67-row `edge67` fixture (tail-byte and NULL-cross coverage).
pub fn edge67_cases() -> Vec<Case> {
    vec![
        // Two independently NULL columns crossed: exercises the Kleene AND on
        // every word and the 3 tail bits.
        Case::ok(
            "cross-null-and-on-67-rows",
            json!({"op":"and","args":[
                {"op":"cmp","column":"a","cmp":"=","value":0},
                {"op":"cmp","column":"b","cmp":"=","value":0}
            ]}),
            None,
        ),
        Case::ok(
            "cross-null-or-on-67-rows",
            json!({"op":"or","args":[
                {"op":"cmp","column":"a","cmp":"=","value":5},
                {"op":"cmp","column":"b","cmp":"=","value":2}
            ]}),
            None,
        ),
        // NOT over the non-word-sized universe: the last bits are 2 real rows
        // plus 61 padding positions that must stay zero.
        Case::ok(
            "not-over-tail-bits",
            json!({"op":"not","arg":
                {"op":"cmp","column":"a","cmp":">=","value":2}}),
            None,
        ),
        // Range scan straddling the deleted tail row 66.
        Case::ok(
            "id-range-over-tail",
            json!({"op":"and","args":[
                {"op":"cmp","column":"id","cmp":">=","value":60},
                {"op":"cmp","column":"id","cmp":"<=","value":66}
            ]}),
            None,
        ),
        // Text comparison on the tail rows.
        Case::ok(
            "grp-eq-tail",
            json!({"op":"cmp","column":"grp","cmp":"=","value":"k1"}),
            None,
        ),
        // The v1 snapshot includes tail row 66; v2 excludes it.
        Case::ok(
            "tail-row-live-at-v1",
            json!({"op":"cmp","column":"id","cmp":"=","value":66}),
            Some(1),
        ),
        Case::ok(
            "tail-row-deleted-at-v2",
            json!({"op":"cmp","column":"id","cmp":"=","value":66}),
            Some(2),
        ),
        // IS NULL OR-ed across both nullable columns.
        Case::ok(
            "either-null-67",
            json!({"op":"or","args":[
                {"op":"is_null","column":"a"},
                {"op":"is_null","column":"b"}
            ]}),
            None,
        ),
    ]
}
