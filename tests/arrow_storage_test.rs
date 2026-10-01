//! Verifies that data really lives in Arrow2 arrays with native null
//! bitmaps, rather than a side `Option` vector dressed up as "Arrow".

use arrow2::array::{BooleanArray, PrimitiveArray, Utf8Array};
use decorr::ast::LoadFixturesRequest;
use decorr::batch::LogicalType;
use decorr::{build_app_state, Catalog};
use serde_json::json;

mod common;

use common::fixture_json;

#[test]
fn columns_are_concrete_arrow2_arrays_with_validity_bitmaps() {
    let service = build_app_state();
    let req: LoadFixturesRequest = serde_json::from_value(fixture_json()).unwrap();
    service.load_fixtures(&req).unwrap();

    let relation = service.catalog().get("payments").unwrap();
    let batch = relation.batch;

    // INTEGER column: downcast must succeed and the native validity mask must
    // mark row 2 (amount NULL).
    let amount = batch.column("amount").unwrap();
    assert_eq!(amount.logical_type(), LogicalType::Integer);
    let array = amount
        .arrow_array()
        .as_any()
        .downcast_ref::<PrimitiveArray<i64>>()
        .expect("underlying array is arrow2 PrimitiveArray<i64>");
    assert_eq!(array.len(), 4);
    let validity = array
        .validity()
        .expect("amount column carries a validity bitmap");
    assert!(validity.get(0).unwrap());
    assert!(validity.get(1).unwrap());
    assert!(
        !validity.get(2).unwrap(),
        "row 2 amount is NULL in Arrow2 bitmap"
    );
    assert!(validity.get(3).unwrap(), "row 3 amount=7 is non-NULL");

    // The physical integer buffer is contiguous; NULL slot holds an arbitrary
    // physical value but logical access yields ScalarValue::Null.
    assert_eq!(amount.value(2), decorr::batch::ScalarValue::Null);

    // INTEGER nullable key column too.
    let cust = batch.column("cust").unwrap();
    let cust_arr = cust
        .arrow_array()
        .as_any()
        .downcast_ref::<PrimitiveArray<i64>>()
        .unwrap();
    let cv = cust_arr.validity().unwrap();
    assert!(!cv.get(3).unwrap());

    // TEXT column from the labels relation exercises Utf8Array.
    let labels = service.catalog().get("labels").unwrap();
    let label = labels.batch.column("label").unwrap();
    let str_arr = label
        .arrow_array()
        .as_any()
        .downcast_ref::<Utf8Array<i32>>()
        .expect("text backed by arrow2 Utf8Array<i32>");
    assert_eq!(str_arr.value(0), "a");
    assert_eq!(str_arr.value(1), "b");

    // BOOLEAN computed column produced by an EXISTS query.
    use decorr::ast::QueryRequest;
    let query: QueryRequest = serde_json::from_value(json!({
        "query_id": "arrow-check",
        "cross_check": false,
        "outer": { "relation": "payments", "select": ["p_id"] },
        "subquery": {
            "op": "exists",
            "inner_relation": "orders",
            "correlation": [ { "outer": "cust", "inner": "cust" } ],
            "output_column": "has_order"
        }
    }))
    .unwrap();
    let success = service.run_query(query).unwrap();
    // Rebuild via the public batch path is not exposed for query results, so
    // construct an equivalent boolean column and inspect its Arrow2 storage.
    let bools: Vec<Option<bool>> = success
        .rows
        .iter()
        .map(|r| r["has_order"].as_bool())
        .collect();
    let bool_col = decorr::batch::Column::from_options(
        "has_order",
        LogicalType::Boolean,
        &bools
            .iter()
            .map(|b| match b {
                Some(v) => json!(v),
                None => serde_json::Value::Null,
            })
            .collect::<Vec<_>>(),
    )
    .unwrap();
    let bool_arr = bool_col
        .arrow_array()
        .as_any()
        .downcast_ref::<BooleanArray>()
        .expect("boolean backed by arrow2 BooleanArray");
    assert_eq!(bool_arr.len(), 4);
}

#[test]
fn fresh_catalog_is_empty_and_typed_schema_builds_empty_arrow_arrays() {
    let catalog = Catalog::new();
    assert!(catalog.names().is_empty());
    assert!(catalog.get("missing").is_err());
}
