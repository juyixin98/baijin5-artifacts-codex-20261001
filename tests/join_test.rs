//! Hash join correctness against the independent nested-loop oracle, plus the
//! key-type-mismatch input error and full child shutdown.

mod common;

use common::{i, s};
use pull_query::batch::Scalar;
use pull_query::cancel::{CancellationToken, Control};
use pull_query::diag::RunDiag;
use pull_query::error::ErrorKind;
use pull_query::fixture;
use pull_query::operator::run_to_completion;
use pull_query::operators::{batch_rows, HashJoin, JoinType, Scan};
use pull_query::oracle;

fn int(v: i64) -> Scalar {
    i(v)
}

#[test]
fn join_matches_independent_nested_loop_oracle() {
    // Left: (lk, lv). Right: (rk, rv).
    use pull_query::batch::{BatchBuilder, ColumnType, Schema};
    use std::sync::Arc;
    let lsch = Arc::new(Schema::new(vec![
        ("lk".to_string(), ColumnType::Int),
        ("lv".to_string(), ColumnType::Utf8),
    ]));
    let rsch = Arc::new(Schema::new(vec![
        ("rk".to_string(), ColumnType::Int),
        ("rv".to_string(), ColumnType::Utf8),
    ]));

    // Includes: duplicate keys (fan-out), an unmatched left key (99), and a
    // null left key that must not match.
    let lrows = vec![
        vec![int(1), s("a1")],
        vec![int(2), s("a2")],
        vec![int(1), s("a1b")],
        vec![int(99), s("unmatched")],
        vec![Scalar::Int(None), s("nullkey")],
    ];
    // Right has duplicate key 1 (fan-out), key 2, and a null key.
    let rrows = vec![
        vec![int(1), s("r1")],
        vec![int(1), s("r1b")],
        vec![int(2), s("r2")],
        vec![Scalar::Int(None), s("rnull")],
    ];

    let mk = |sch: Arc<Schema>, rows: &[Vec<Scalar>]| {
        let mut b = BatchBuilder::new(sch);
        for r in rows {
            b.add_row(r).unwrap();
        }
        b.finish().unwrap()
    };
    let left = Scan::new("left", lsch.clone(), vec![mk(lsch, &lrows)]);
    let right = Scan::new("right", rsch.clone(), vec![mk(rsch, &rrows)]);

    let join = HashJoin::new(
        "join",
        Box::new(left),
        Box::new(right),
        &["lk".to_string()],
        &["rk".to_string()],
        JoinType::Inner,
    )
    .unwrap();

    let diag = RunDiag::new();
    let ctrl = Control::new(CancellationToken::new(), None, diag);
    let mut join = join;
    let (out, err) = run_to_completion(&mut join, &ctrl);
    assert!(err.is_none(), "{err:?}");

    let mut got = Vec::new();
    for b in &out {
        got.extend(batch_rows(b).unwrap());
    }

    // Expected from the independent oracle (lk col 0, rk col 0).
    let expected = oracle::inner_join(&lrows, &rrows, &[0], &[0]);
    assert_eq!(got.len(), expected.len());
    assert_eq!(got, expected);

    // Explicit fan-out counts: key 1 appears 2 left x 2 right = 4 rows.
    let key1 = got
        .iter()
        .filter(|r| matches!(r[0], Scalar::Int(Some(1))))
        .count();
    assert_eq!(key1, 4);
}

#[test]
fn join_uses_fixture_and_unmatched_probe_rows_are_dropped() {
    // orders(100 rows, user ids cycle 1..=9) joined to 8 users: id 9 unmatched.
    let oschema = fixture::orders_schema();
    let orows = fixture::orders_rows(18); // two full 1..=9 cycles → 2 unmatched
    let obatch = {
        let mut b = pull_query::batch::BatchBuilder::new(oschema.clone());
        for r in &orows {
            b.add_row(r).unwrap();
        }
        b.finish().unwrap()
    };
    let ubatch = fixture::users_batch().unwrap();
    let left = Scan::new("orders", oschema, vec![obatch]);
    let right = Scan::new("users", fixture::users_schema(), vec![ubatch]);

    let join = HashJoin::new(
        "join",
        Box::new(left),
        Box::new(right),
        &["user_id".to_string()],
        &["id".to_string()],
        JoinType::Inner,
    )
    .unwrap();

    let diag = RunDiag::new();
    let ctrl = Control::new(CancellationToken::new(), None, diag);
    let mut join = join;
    let (out, err) = run_to_completion(&mut join, &ctrl);
    assert!(err.is_none(), "{err:?}");
    let mut got = Vec::new();
    for b in &out {
        got.extend(batch_rows(b).unwrap());
    }

    let urows = fixture::users_rows();
    let expected = oracle::inner_join(&orows, &urows, &[1], &[0]);
    assert_eq!(got, expected);
    assert_eq!(got.len(), 16); // 18 orders - 2 with absent user id 9.
}

#[test]
fn join_rejects_mismatched_key_types_as_invalid_input() {
    use pull_query::batch::{BatchBuilder, ColumnType, Schema};
    use std::sync::Arc;
    let lsch = Arc::new(Schema::new(vec![("k".to_string(), ColumnType::Int)]));
    let rsch = Arc::new(Schema::new(vec![("k".to_string(), ColumnType::Utf8)]));
    let mut lbb = BatchBuilder::new(lsch);
    lbb.add_row(&[int(1)]).unwrap();
    let lb = lbb.finish().unwrap();
    let mut rbb = BatchBuilder::new(rsch);
    rbb.add_row(&[s("1")]).unwrap();
    let rb = rbb.finish().unwrap();
    let left = Scan::new("l", lb.schema().clone(), vec![lb]);
    let right = Scan::new("r", rb.schema().clone(), vec![rb]);
    let err = HashJoin::new(
        "join",
        Box::new(left),
        Box::new(right),
        &["k".to_string()],
        &["k".to_string()],
        JoinType::Inner,
    )
    .err()
    .unwrap();
    assert_eq!(err.kind(), ErrorKind::InvalidInput);
}
