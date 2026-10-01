//! Versioned deletes: deleted rows excluded from TRUE/FALSE/UNKNOWN, versions
//! bump on commit, combined indexes built over different delete sets are
//! rejected with DELETESET_MISMATCH.

mod common;

use std::collections::HashSet;

use common::{cmp, eval_scalar, init_tracing, intv, people_spec, rows_to_json, OraRow};
use tribool_index::error::ErrorKind;
use tribool_index::logic::Tri3;
use tribool_index::query::{run, Expr};
use tribool_index::state::Catalog;

fn rows() -> Vec<OraRow> {
    // 17 rows: non-multiple of 8 on purpose.
    (0..17)
        .map(|i| OraRow {
            city: if i % 3 == 0 {
                Some("BJ".into())
            } else {
                Some("SH".into())
            },
            age: if i % 5 == 0 {
                None
            } else {
                Some(10 + i as i64)
            },
            score: Some(i as i64),
            active: Some(i % 2 == 0),
        })
        .collect()
}

#[test]
fn deleted_rows_are_outside_all_three_sets() {
    init_tracing();
    let run_id = "test-deletes-excluded";
    let data = rows();
    let mut catalog = Catalog::new();
    let table = catalog
        .create_table("t", people_spec(), rows_to_json(&data))
        .unwrap();

    let v0 = table.read().unwrap().version_info();
    assert_eq!(
        (v0.version, v0.total_rows, v0.alive_rows, v0.deleted_rows),
        (1, 17, 17, 0)
    );

    // Delete a NULL-age row (5), a TRUE candidate (1 -> age 11 >= 11) and a
    // high tail row (16, in the final partial byte).
    let deleted = [1usize, 5, 16];
    let info = {
        let mut t = table.write().unwrap();
        t.delete_rows(&deleted).unwrap()
    };
    assert_eq!(info.version, 2);
    assert_eq!(info.alive_rows, 14);
    assert_eq!(info.deleted_rows, 3);

    let expr = Expr::And {
        args: vec![
            cmp("age", ">=", intv(11)),
            cmp("city", "=", common::txtv("BJ")),
        ],
    };
    let result = {
        let t = table.read().unwrap();
        run(&t, &expr, run_id).unwrap()
    };
    assert_eq!(result.version, 2, "query reports the version it ran at");

    let deleted_set: HashSet<usize> = deleted.iter().copied().collect();
    let mut classified = 0usize;
    for (i, row) in data.iter().enumerate() {
        let state = result.tris.state_at(i);
        if deleted_set.contains(&i) {
            assert_eq!(state, None, "deleted row {i} must be outside T/F/U");
        } else {
            let state = state.unwrap_or_else(|| panic!("alive row {i} unclassified"));
            classified += 1;
            let expected = eval_scalar(&expr, row);
            match (expected, state) {
                (common::Ora::T, Tri3::True)
                | (common::Ora::F, Tri3::False)
                | (common::Ora::U, Tri3::Unknown) => {}
                other => panic!("row {i}: oracle {expected:?} vs {other:?}"),
            }
        }
    }
    assert_eq!(classified, 14);
    assert_eq!(
        result.tris.count_true() + result.tris.count_false() + result.tris.count_unknown(),
        14
    );
    tracing::info!(run_id, version = 2, deleted = ?deleted, "deleted rows absent from every set");
}

#[test]
fn combining_indexes_from_different_delete_versions_is_rejected() {
    init_tracing();
    let run_id = "test-deleteset-mismatch";
    let data = rows();
    let mut catalog = Catalog::new();
    let table = catalog
        .create_table("t", people_spec(), rows_to_json(&data))
        .unwrap();

    // Snapshot A: row 2 deleted. The write guard MUST be released before
    // acquiring a read guard (std::sync::RwLock is not reentrant).
    let snapshot_a = {
        table.write().unwrap().delete_rows(&[2]).unwrap();
        let t = table.read().unwrap();
        let alive = t.alive();
        t.indexes()
            .get("age")
            .unwrap()
            .evaluate(
                tribool_index::index::CmpOp::Gte,
                Some(&tribool_index::index::Literal::Int(0)),
                &alive,
            )
            .unwrap()
    };
    // Snapshot B: another deletion -> different alive set.
    let snapshot_b = {
        table.write().unwrap().delete_rows(&[3]).unwrap();
        let t = table.read().unwrap();
        let alive = t.alive();
        t.indexes()
            .get("active")
            .unwrap()
            .evaluate(
                tribool_index::index::CmpOp::Eq,
                Some(&tribool_index::index::Literal::Bool(true)),
                &alive,
            )
            .unwrap()
    };

    let err = snapshot_a.and(&snapshot_b).unwrap_err();
    assert_eq!(
        err.kind,
        ErrorKind::DeletesetMismatch,
        "got {}: {err}",
        err.code()
    );
    let err = snapshot_a.or(&snapshot_b).unwrap_err();
    assert_eq!(err.kind, ErrorKind::DeletesetMismatch);
    tracing::info!(run_id, code = err.code(), "incompatible modes rejected");
}

#[test]
fn delete_versioning_is_monotonic_and_idempotent() {
    init_tracing();
    let data = rows();
    let mut catalog = Catalog::new();
    let table = catalog
        .create_table("t", people_spec(), rows_to_json(&data))
        .unwrap();

    let v1 = {
        let mut t = table.write().unwrap();
        t.delete_rows(&[0, 1]).unwrap()
    };
    assert_eq!(v1.version, 2);
    let v2 = {
        let mut t = table.write().unwrap();
        t.delete_rows(&[1]).unwrap() // already deleted
    };
    assert_eq!(v2.version, 2, "idempotent delete does not bump version");
    assert_eq!(v2.deleted_rows, 2);
    let v3 = {
        let mut t = table.write().unwrap();
        t.delete_rows(&[2]).unwrap()
    };
    assert_eq!(v3.version, 3);
    assert_eq!(v3.deleted_rows, 3);

    // Out-of-range ids fail loudly.
    let err = table.write().unwrap().delete_rows(&[17]).unwrap_err();
    assert_eq!(err.kind, ErrorKind::InvalidInput);
    let err = table.write().unwrap().delete_rows(&[100]).unwrap_err();
    assert_eq!(err.kind, ErrorKind::InvalidInput);
}

#[test]
fn not_after_delete_stays_inside_alive_universe() {
    init_tracing();
    let run_id = "test-not-after-delete";
    let data = rows();
    let mut catalog = Catalog::new();
    let table = catalog
        .create_table("t", people_spec(), rows_to_json(&data))
        .unwrap();
    // Delete the tail rows (15,16) — exactly where a word-NOT would create
    // phantom matches.
    {
        let mut t = table.write().unwrap();
        t.delete_rows(&[15, 16]).unwrap();
    }
    let expr = Expr::Not {
        arg: Box::new(cmp("age", "=", intv(20))),
    };
    let result = {
        let t = table.read().unwrap();
        run(&t, &expr, run_id).unwrap()
    };
    assert_eq!(result.tris.state_at(15), None);
    assert_eq!(result.tris.state_at(16), None);
    // TRUE(NOT x) rows are exactly the alive non-NULL rows with age != 20.
    for (i, row) in data.iter().enumerate() {
        match result.tris.state_at(i) {
            None => assert!(i == 15 || i == 16),
            Some(state) => {
                let expected = eval_scalar(&expr, row);
                let ok = matches!(
                    (expected, state),
                    (common::Ora::T, Tri3::True)
                        | (common::Ora::F, Tri3::False)
                        | (common::Ora::U, Tri3::Unknown)
                );
                assert!(ok, "row {i}: {expected:?} vs {state:?}");
            }
        }
    }
    // Byte 2 covers rows 16..23: row 16 is deleted and 17..23 are padding, so
    // it must be entirely zero in every output set.
    let unknown = result.tris.unknown_set();
    for (label, bm) in [
        ("true", result.tris.true_set()),
        ("false", result.tris.false_set()),
        ("unknown", &unknown),
    ] {
        assert_eq!(bm.as_bytes()[2], 0, "{label} set: byte 2 must be zero");
    }
}
