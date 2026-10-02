//! Typed-batch contract: construction validation and row extraction.

mod common;

use common::{batch, i, s, schema};
use pull_query::batch::{BatchBuilder, ColumnType, Scalar};
use pull_query::error::ErrorKind;

#[test]
fn builds_well_typed_batch_and_reads_scalars() {
    let sch = schema(&[
        ("id", ColumnType::Int),
        ("name", ColumnType::Utf8),
        ("active", ColumnType::Bool),
    ]);
    let rows = vec![
        vec![i(1), s("ada"), Scalar::Bool(Some(true))],
        vec![Scalar::Int(None), Scalar::Utf8(None), Scalar::Bool(None)],
    ];
    let b = batch(sch.clone(), &rows);
    assert_eq!(b.num_rows(), 2);

    let ids = b.column_scalars(0).unwrap();
    assert_eq!(ids, vec![Scalar::Int(Some(1)), Scalar::Int(None)]);
    let names = b.column_scalars(1).unwrap();
    assert_eq!(
        names,
        vec![Scalar::Utf8(Some("ada".to_string())), Scalar::Utf8(None)]
    );
    let active = b.column_scalars(2).unwrap();
    assert_eq!(active, vec![Scalar::Bool(Some(true)), Scalar::Bool(None)]);
}

#[test]
fn rejects_scalar_of_wrong_type_at_build_time() {
    let sch = schema(&[("id", ColumnType::Int)]);
    let mut b = BatchBuilder::new(sch);
    let err = b.add_row(&[s("not-an-int")]).err().unwrap();
    assert_eq!(err.kind(), ErrorKind::InvalidInput);
}

#[test]
fn rejects_ragged_and_wrong_width_batches() {
    let sch = schema(&[("a", ColumnType::Int), ("b", ColumnType::Int)]);
    let mut b = BatchBuilder::new(sch);
    let err = b.add_row(&[i(1)]).err().unwrap();
    assert_eq!(err.kind(), ErrorKind::InvalidInput);

    // A hand-built array set with mismatched column count is rejected too.
    use arrow2::array::{Array, PrimitiveArray};
    let arr = PrimitiveArray::<i64>::from_slice([1, 2, 3]).to_boxed();
    let sch2 = std::sync::Arc::new(pull_query::Schema::new(vec![
        ("a".to_string(), ColumnType::Int),
        ("b".to_string(), ColumnType::Int),
    ]));
    let err = pull_query::Batch::try_new(sch2, vec![arr]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::InvalidInput);
}

#[test]
fn projection_reorders_and_selects() {
    let sch = schema(&[
        ("a", ColumnType::Int),
        ("b", ColumnType::Utf8),
        ("c", ColumnType::Int),
    ]);
    let b = batch(sch, &[vec![i(1), s("x"), i(3)]]);
    let p = b.project(&[2, 0]).unwrap();
    assert_eq!(
        p.schema()
            .fields()
            .iter()
            .map(|(n, _)| n.as_str())
            .collect::<Vec<_>>(),
        vec!["c", "a"]
    );
    assert_eq!(p.column_scalars(0).unwrap(), vec![Scalar::Int(Some(3))]);
}
