//! Arrow IPC round-trip: run a join through the Arrow endpoint pipeline, then
//! decode the produced stream with arrow2's own reader and assert the schema
//! and concrete values. This verifies the IPC bytes without external tools.
use std::io::Cursor;

use arrow2::array::PrimitiveArray;
use arrow2::io::ipc::read::{read_stream_metadata, StreamReader, StreamState};
use leapfrog_triejoin::domain::{Datum, NullPolicy};
use leapfrog_triejoin::query::JoinRequest;
use leapfrog_triejoin::{build_default_catalog, run_arrow, AppState, ServerConfig};

fn state() -> AppState {
    AppState::new(ServerConfig::default(), build_default_catalog())
}

#[test]
fn ipc_stream_decodes_to_expected_sparse_rows() {
    let req = JoinRequest {
        relations: vec![],
        fixtures: vec!["sp_x".to_string(), "sp_y".to_string()],
        select: None,
        limit: 1000,
        cursor: None,
        null_policy: NullPolicy::Reject,
        request_id: Some("arrow-rt".to_string()),
    };

    let (bytes, meta) = run_arrow(&state(), req).expect("arrow pipeline succeeds");
    assert_eq!(meta.row_count, 3);

    let mut reader = Cursor::new(bytes.as_slice());
    let metadata = read_stream_metadata(&mut reader).expect("read stream metadata");
    assert_eq!(
        metadata
            .schema
            .fields
            .iter()
            .map(|f| f.name.as_str())
            .collect::<Vec<_>>(),
        vec!["k", "v", "w"]
    );

    let stream = StreamReader::new(reader, metadata, None);
    let mut decoded_keys: Vec<i64> = Vec::new();
    let mut batches = 0;
    for state in stream {
        match state.expect("valid stream state") {
            StreamState::Some(chunk) => {
                batches += 1;
                let key_col = chunk.columns()[0]
                    .as_any()
                    .downcast_ref::<PrimitiveArray<i64>>()
                    .expect("k is int64");
                for v in key_col.iter() {
                    decoded_keys.push(*v.unwrap());
                }
            }
            StreamState::Waiting => continue,
        }
    }

    assert_eq!(batches, 1, "one chunk was written");
    assert_eq!(decoded_keys, vec![5, 7, 9]);
}

#[test]
fn ipc_stream_handles_nullable_private_values() {
    // Relation with an explicit NULL in a *private* (non-join) column: the
    // arrow column must be nullable and the null must survive the round trip.
    use leapfrog_triejoin::batch::{ColumnSchema, RelationSchema, TypedBatch};
    use std::sync::Arc;

    let l_schema = Arc::new(RelationSchema::new(vec![
        ColumnSchema::new("k", leapfrog_triejoin::domain::LogicalType::Int64),
        ColumnSchema::new("y", leapfrog_triejoin::domain::LogicalType::Int64),
    ]));
    let r_schema = Arc::new(RelationSchema::new(vec![ColumnSchema::new(
        "k",
        leapfrog_triejoin::domain::LogicalType::Int64,
    )]));
    let mut catalog = leapfrog_triejoin::Catalog::new();
    catalog.insert(
        "nl",
        l_schema.clone(),
        TypedBatch::from_rows(
            l_schema,
            vec![
                vec![Datum::Int(1), Datum::Null],
                vec![Datum::Int(2), Datum::Int(20)],
            ],
        )
        .unwrap(),
    );
    catalog.insert(
        "nr",
        r_schema.clone(),
        TypedBatch::from_rows(r_schema, vec![vec![Datum::Int(1)], vec![Datum::Int(2)]]).unwrap(),
    );
    let st = AppState::new(ServerConfig::default(), catalog);

    // NULL is on private column y (not the join key k), so reject policy passes.
    let req = JoinRequest {
        relations: vec![],
        fixtures: vec!["nl".to_string(), "nr".to_string()],
        select: None,
        limit: 100,
        cursor: None,
        null_policy: NullPolicy::Reject,
        request_id: None,
    };

    let (bytes, _) = run_arrow(&st, req).expect("join with null private col");
    let mut reader = Cursor::new(bytes.as_slice());
    let metadata = read_stream_metadata(&mut reader).unwrap();
    let stream = StreamReader::new(reader, metadata, None);
    let mut null_seen = false;
    for state in stream {
        if let StreamState::Some(chunk) = state.unwrap() {
            let y = chunk.columns()[1]
                .as_any()
                .downcast_ref::<PrimitiveArray<i64>>()
                .unwrap();
            null_seen |= y.iter().any(|v| v.is_none());
        }
    }
    assert!(null_seen, "NULL private value must round-trip through IPC");
}
