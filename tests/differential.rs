//! Differential tests: engine vs the independent scalar oracle.
//!
//! These are integration tests (separate crate, only public API). They assert
//! concrete expected rows for hand-computed cases, exhaustively cross-check a
//! generated family of boolean formula trees, and pin the exact failure
//! categories. Expected answers are NOT produced by the engine.

use std::collections::HashSet;
use std::path::Path;

use serde_json::{json, Value};
use tvindex::query::executor::{self, Catalog};
use tvindex::query::Expr;
use tvindex::verify::oracle::OT;
use tvindex::verify::runner::{run_suite, Case};

fn people_paths() -> (&'static Path, &'static Path) {
    (Path::new("fixtures/people.toml"), Path::new("fixtures"))
}

fn edge_paths() -> (&'static Path, &'static Path) {
    (Path::new("fixtures/edge67.toml"), Path::new("fixtures"))
}

/// (label, predicate JSON, expected TRUE/FALSE/UNKNOWN rows).
type RowExpectation = (&'static str, Value, Vec<usize>, Vec<usize>, Vec<usize>);

#[test]
fn builtin_suites_match_oracle_with_zero_discrepancies() {
    for (manifest, dir) in [people_paths(), edge_paths()] {
        let (store, oracle) = tvindex::verify::load_both_paths(manifest, dir);
        let cases = if manifest.ends_with("people.toml") {
            tvindex::verify::cases::people_cases()
        } else {
            tvindex::verify::cases::edge67_cases()
        };
        let report = run_suite(&store, &oracle, &cases);
        let failures = report.failures();
        assert!(
            failures.is_empty(),
            "differential failures in {}:\n{}",
            manifest.display(),
            report.render_failures()
        );
    }
}

#[test]
fn people_concrete_true_false_unknown_rows() {
    // Hand-computed from fixtures/people.csv (rows 0..6; row 6 deleted at v2):
    //   0 age=30 name=Ada  active=true  note=NULL
    //   1 age=17 name=NULL active=false note=NULL
    //   2 age=NULL name=Bo active=true  note=NULL
    //   3 age=40 name=Cy   active=NULL  note=NULL
    //   4 age=NULL name=NULL active=true note=NULL
    //   5 age=35 name=Di  active=false note=NULL
    //   6 DELETED at v2
    let (store, _oracle) =
        tvindex::verify::load_both_paths(Path::new("fixtures/people.toml"), Path::new("fixtures"));

    let cases: Vec<RowExpectation> = vec![
        // age = 30: T{0}, F{1,3,5}, U{2,4}
        (
            "age=30",
            json!({"op":"cmp","column":"age","cmp":"=","value":30}),
            vec![0],
            vec![1, 3, 5],
            vec![2, 4],
        ),
        // NOT(age = 30): swaps T/F, U fixed point.
        (
            "not age=30",
            json!({"op":"not","arg":{"op":"cmp","column":"age","cmp":"=","value":30}}),
            vec![1, 3, 5],
            vec![0],
            vec![2, 4],
        ),
        // age >= 18 AND active = true:
        //  row0 T&T=T; row2 U&T=U; row4 U&T=U; rows1(F&F)=F,3(T&U)=U,5(T&F)=F
        (
            "age>=18 and active",
            json!({"op":"and","args":[
                {"op":"cmp","column":"age","cmp":">=","value":18},
                {"op":"cmp","column":"active","cmp":"=","value":true}]}),
            vec![0],
            vec![1, 5],
            vec![2, 3, 4],
        ),
        // age >= 35 OR name IS NULL:
        //  row0 F|F=F; row1 F|T=T; row2 U|F=U; row3 T|F=T; row4 U|T=T; row5 T|F=T
        (
            "age>=35 or name null",
            json!({"op":"or","args":[
                {"op":"cmp","column":"age","cmp":">=","value":35},
                {"op":"is_null","column":"name"}]}),
            vec![1, 3, 4, 5],
            vec![0],
            vec![2],
        ),
        // note = 'x' on the all-NULL column: every live row UNKNOWN.
        (
            "all unknown column",
            json!({"op":"cmp","column":"note","cmp":"=","value":"x"}),
            vec![],
            vec![],
            vec![0, 1, 2, 3, 4, 5],
        ),
        // note IS NULL: UNKNOWN collapses to TRUE.
        (
            "all null is_null",
            json!({"op":"is_null","column":"note"}),
            vec![0, 1, 2, 3, 4, 5],
            vec![],
            vec![],
        ),
    ];

    let catalog = Catalog::new(store.indexes(), &store.versions).unwrap();
    for (label, expr_json, want_t, want_f, want_u) in cases {
        let expr = Expr::parse(&expr_json).unwrap();
        let out = executor::evaluate(&catalog, &expr, None, executor::next_run_id()).unwrap();
        assert_eq!(out.true_rows, want_t, "TRUE rows wrong for {label}");
        assert_eq!(out.false_rows, want_f, "FALSE rows wrong for {label}");
        assert_eq!(out.unknown_rows, want_u, "UNKNOWN rows wrong for {label}");
        assert_eq!(out.deleted_rows, vec![6], "deleted rows wrong for {label}");
        // WHERE selects TRUE only.
        assert_eq!(
            out.selected(),
            out.true_rows,
            "selected != TRUE for {label}"
        );
        // Every live row belongs to exactly one class.
        let partition: HashSet<usize> = out
            .true_rows
            .iter()
            .chain(&out.false_rows)
            .chain(&out.unknown_rows)
            .copied()
            .collect();
        assert_eq!(
            partition.len(),
            out.live_total,
            "partition not exact-once: {label}"
        );
    }
}

#[test]
fn time_travel_shows_deleted_row_with_unknown_age() {
    let (store, _) =
        tvindex::verify::load_both_paths(Path::new("fixtures/people.toml"), Path::new("fixtures"));
    let catalog = Catalog::new(store.indexes(), &store.versions).unwrap();
    let expr = Expr::parse(&json!({"op":"cmp","column":"age","cmp":"=","value":1})).unwrap();

    // At v1 row 6 is live and its NULL age is UNKNOWN.
    let v1 = executor::evaluate(&catalog, &expr, Some(1), executor::next_run_id()).unwrap();
    assert_eq!(v1.live_total, 7);
    assert!(v1.unknown_rows.contains(&6));
    assert!(!v1.deleted_rows.contains(&6));

    // At head (v2) it is deleted and appears in no truth class.
    let v2 = executor::evaluate(&catalog, &expr, Some(2), executor::next_run_id()).unwrap();
    assert_eq!(v2.live_total, 6);
    assert_eq!(v2.deleted_rows, vec![6]);
    for r in v2
        .true_rows
        .iter()
        .chain(&v2.false_rows)
        .chain(&v2.unknown_rows)
    {
        assert_ne!(*r, 6, "deleted row 6 must not be classified at v2");
    }
}

#[test]
fn failure_categories_are_concrete_not_just_callable() {
    let (store, oracle) =
        tvindex::verify::load_both_paths(Path::new("fixtures/people.toml"), Path::new("fixtures"));
    let cases = vec![
        Case::error(
            "null literal",
            json!({"op":"cmp","column":"age","cmp":"=","value":null}),
            None,
            "invalid_query",
        ),
        Case::error(
            "unknown column",
            json!({"op":"cmp","column":"ghost","cmp":"=","value":1}),
            None,
            "unknown_column",
        ),
        Case::error(
            "type mismatch",
            json!({"op":"cmp","column":"age","cmp":"=","value":"1"}),
            None,
            "type_error",
        ),
        Case::error(
            "bad operator",
            json!({"op":"cmp","column":"age","cmp":"??","value":1}),
            None,
            "invalid_query",
        ),
        Case::error(
            "empty or",
            json!({"op":"or","args":[]}),
            None,
            "invalid_query",
        ),
        Case::error(
            "future as_of",
            json!({"op":"is_null","column":"age"}),
            Some(42),
            "invalid_query",
        ),
    ];
    for c in &cases {
        let report = tvindex::verify::run_case(&store, &oracle, c);
        assert!(
            report.passed(),
            "case {} failed:\n{}",
            c.id,
            report.render()
        );
    }
}

#[test]
fn exhaustive_boolean_formulas_match_oracle_on_edge67() {
    // Enumerate every boolean formula up to depth 2 over a fixed leaf set,
    // and require per-row equality with the scalar oracle across all 67 rows.
    // Leaves deliberately include NULL-producing comparisons in both columns
    // and on tail rows.
    let (store, oracle) = tvindex::verify::load_both_paths(edge_paths().0, edge_paths().1);
    let leaves: Vec<Value> = vec![
        json!({"op":"cmp","column":"a","cmp":"=","value":0}),
        json!({"op":"cmp","column":"a","cmp":">=","value":3}),
        json!({"op":"cmp","column":"b","cmp":"=","value":2}),
        json!({"op":"cmp","column":"b","cmp":"<","value":2}),
        json!({"op":"is_null","column":"a"}),
        json!({"op":"is_null","column":"b"}),
        json!({"op":"cmp","column":"grp","cmp":"=","value":"k1"}),
    ];

    // depth-1: NOT(leaf)
    let mut formulas: Vec<Value> = leaves.clone();
    for l in &leaves {
        formulas.push(json!({"op":"not","arg":l}));
    }
    // depth-2: every ordered AND/OR pair (including self) at this size.
    for (i, a) in leaves.iter().enumerate() {
        for (j, b) in leaves.iter().enumerate() {
            formulas.push(json!({"op":"and","args":[a,b]}));
            formulas.push(json!({"op":"or","args":[a,b]}));
            // NOT over a compound: forces UNKNOWN through NOT after a gate.
            if i <= j {
                formulas.push(json!({"op":"not","arg":{"op":"and","args":[a,b]}}));
                formulas.push(json!({"op":"not","arg":{"op":"or","args":[a,b]}}));
            }
        }
    }

    let catalog = Catalog::new(store.indexes(), &store.versions).unwrap();
    let n = store.table.len();
    assert_eq!(n, 67, "edge67 fixture must keep its non-word size");
    let mut checked = 0usize;
    for f in &formulas {
        let expr = Expr::parse(f).unwrap();
        let out = executor::evaluate(&catalog, &expr, None, executor::next_run_id()).unwrap();
        let engine: std::collections::HashMap<usize, OT> = out
            .classifications
            .iter()
            .map(|(r, ch)| {
                (
                    *r,
                    match ch {
                        'T' => OT::True,
                        'F' => OT::False,
                        _ => OT::Unknown,
                    },
                )
            })
            .collect();
        for row in 0..n {
            let live = oracle.is_live_at(row, 2);
            let expected = oracle.eval_row(f, row).unwrap();
            if live {
                assert_eq!(
                    engine[&row], expected,
                    "formula {f} diverges at row {row} (tail region)"
                );
            } else {
                assert!(
                    !engine.contains_key(&row),
                    "deleted row {row} classified for formula {f}"
                );
            }
        }
        // Exact-once over the live universe even in the tail word.
        assert_eq!(
            out.true_rows.len() + out.false_rows.len() + out.unknown_rows.len(),
            out.live_total,
            "partition broken for {f}"
        );
        checked += 1;
    }
    // Family size: 7 leaves + 7 NOT(leaf) + 49 AND + 49 OR + NOT over the 28
    // i<=j compounds (28 AND + 28 OR) = 168 formulae, all checked per row.
    assert_eq!(checked, 168, "exhaustive formula family changed size");
}

#[test]
fn version_incompatible_index_is_rejected() {
    // The public API enforces combined-index compatibility inside
    // Catalog::new; exercise the contract via VersionMap directly with an
    // explicitly stale content generation.
    use tvindex::index::VersionMap;
    let mut vm = VersionMap::new(10);
    vm.bump_row(0, 2).unwrap(); // content moves to v2
    let err = vm.ensure_compatible(10, 1).unwrap_err();
    assert!(err.to_string().contains("content version"));
    // Deletes alone must NOT break compatibility.
    vm.delete(1, 3).unwrap();
    assert!(vm.ensure_compatible(10, 2).is_ok());
}

#[test]
fn deleted_rows_cannot_sneak_through_not_at_tail() {
    // edge67 row 66 is a tail bit and is deleted at v2; NOT must not resurrect
    // it into FALSE (a machine-word NOT over the true bitmap would).
    let (store, _) = tvindex::verify::load_both_paths(edge_paths().0, edge_paths().1);
    let catalog = Catalog::new(store.indexes(), &store.versions).unwrap();
    let f = json!({"op":"not","arg":
        {"op":"cmp","column":"id","cmp":"=","value":66}});
    let out = executor::evaluate(
        &catalog,
        &Expr::parse(&f).unwrap(),
        Some(2),
        executor::next_run_id(),
    )
    .unwrap();
    assert!(out.deleted_rows.contains(&66));
    assert!(!out.false_rows.contains(&66));
    assert_eq!(out.live_total, 66);
}
