//! Arrow2 evidence: the result is not only returned as JSON but also encoded
//! as an Arrow IPC stream. These tests decode that stream independently with
//! arrow2's reader and assert physical types and values.

mod common;

use std::io::Cursor;

use arrow2::array::{BooleanArray, ListArray, PrimitiveArray, Utf8Array};
use arrow2::io::ipc::read::{read_stream_metadata, StreamReader, StreamState};
use base64::Engine;
use common::{cross_check, CaseBuilder};

fn decode_chunks(
    b64: &str,
) -> (
    arrow2::datatypes::Schema,
    Vec<arrow2::chunk::Chunk<Box<dyn arrow2::array::Array>>>,
) {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64.trim())
        .expect("valid base64");
    let mut reader = Cursor::new(bytes);
    let metadata = read_stream_metadata(&mut reader).expect("ipc metadata");
    let schema = metadata.schema.clone();
    let chunks = StreamReader::new(reader, metadata, None)
        .filter_map(|state| match state.expect("valid stream state") {
            StreamState::Some(chunk) => Some(chunk),
            StreamState::Waiting => None,
        })
        .collect::<Vec<_>>();
    (schema, chunks)
}

#[test]
fn ipc_stream_round_trips_tree_with_declared_physical_types() {
    let edges = vec![(1, 2), (2, 3)];
    let req = CaseBuilder::new("ipc-tree", &edges)
        .include_ipc()
        .limits(8, 100)
        .build();
    let pair = cross_check("ipc-tree", &req);
    let resp = pair.engine.as_ref().unwrap();
    let b64 = resp.arrow_ipc_base64.as_ref().expect("ipc payload present");

    let (schema, chunks) = decode_chunks(b64);
    assert_eq!(chunks.len(), 1, "one chunk for the whole result");
    let chunk = &chunks[0];
    assert_eq!(chunk.len(), 3, "three output rows");

    // Schema physical types.
    let types: Vec<_> = schema
        .fields
        .iter()
        .map(|f| f.data_type().clone())
        .collect();
    use arrow2::datatypes::DataType;
    assert_eq!(types[0], DataType::Int64);
    assert_eq!(types[1], DataType::Int64);
    assert!(
        matches!(&types[2], DataType::LargeList(inner) if inner.data_type() == &DataType::Int64)
    );
    assert_eq!(types[3], DataType::Boolean);
    assert_eq!(
        schema
            .fields
            .iter()
            .map(|f| f.name.as_str())
            .collect::<Vec<_>>(),
        vec!["node", "depth", "path", "is_cycle"]
    );

    // Column values.
    let node = chunk.arrays()[0]
        .as_any()
        .downcast_ref::<PrimitiveArray<i64>>()
        .unwrap();
    assert_eq!(
        node.values_iter().copied().collect::<Vec<_>>(),
        vec![1, 2, 3]
    );

    let depth = chunk.arrays()[1]
        .as_any()
        .downcast_ref::<PrimitiveArray<i64>>()
        .unwrap();
    assert_eq!(
        depth.values_iter().copied().collect::<Vec<_>>(),
        vec![0, 1, 2]
    );

    let marker = chunk.arrays()[3]
        .as_any()
        .downcast_ref::<BooleanArray>()
        .unwrap();
    assert_eq!(
        marker.values_iter().collect::<Vec<_>>(),
        vec![false, false, false]
    );

    // Path list contents, element by element via offsets.
    let paths = chunk.arrays()[2]
        .as_any()
        .downcast_ref::<ListArray<i64>>()
        .unwrap();
    let p0 = paths.value(0);
    let p0 = p0.as_any().downcast_ref::<PrimitiveArray<i64>>().unwrap();
    assert_eq!(p0.values_iter().copied().collect::<Vec<_>>(), vec![1]);
    let p2 = paths.value(2);
    let p2 = p2.as_any().downcast_ref::<PrimitiveArray<i64>>().unwrap();
    assert_eq!(p2.values_iter().copied().collect::<Vec<_>>(), vec![1, 2, 3]);
}

#[test]
fn ipc_stream_encodes_utf8_keys_and_cycle_marker() {
    use recursive_cte_backend::plan::{
        ColumnDecl, ColumnType, CycleConfig, CycleMode, Expr, JoinKey, ProjectionEntry,
        RecursiveTerm, Relation, RunLimits, SetQuantifier, TraversalOrder,
    };
    use serde_json::json;
    use std::collections::BTreeMap;

    // A text-key self loop: a -> a.
    let view = Relation {
        columns: vec![
            ColumnDecl {
                name: "id".into(),
                data_type: ColumnType::Utf8,
            },
            ColumnDecl {
                name: "path".into(),
                data_type: ColumnType::ListUtf8,
            },
            ColumnDecl {
                name: "cyc".into(),
                data_type: ColumnType::Bool,
            },
        ],
        rows: vec![vec![json!("a")]],
    };
    let edges = Relation {
        columns: vec![
            ColumnDecl {
                name: "src".into(),
                data_type: ColumnType::Utf8,
            },
            ColumnDecl {
                name: "dst".into(),
                data_type: ColumnType::Utf8,
            },
        ],
        rows: vec![vec![json!("a"), json!("a")]],
    };
    let mut relations = BTreeMap::new();
    relations.insert("edges".to_string(), edges);

    let req = recursive_cte_backend::RecursiveRequest {
        name: Some("ipc-utf8-self".into()),
        view,
        set_quantifier: SetQuantifier::Union,
        relations,
        recursive_term: RecursiveTerm {
            edges_relation: "edges".into(),
            on: vec![JoinKey {
                recursive: "id".into(),
                edge: "src".into(),
            }],
            project: vec![ProjectionEntry {
                alias: "id".into(),
                value: Expr::EdgeColumn { name: "dst".into() },
            }],
        },
        cycle: Some(CycleConfig {
            mode: CycleMode::Mark,
            key_column: "id".into(),
            path_column: "path".into(),
            cycle_column: "cyc".into(),
        }),
        limits: RunLimits {
            max_depth: 8,
            max_rows: 100,
        },
        traversal_order: TraversalOrder::Bfs,
        include_log: false,
        include_arrow_ipc: true,
    };

    let pair = common::run_pair("ipc-utf8-self", &req);
    let resp = pair.engine.as_ref().unwrap();
    let (schema, chunks) = decode_chunks(resp.arrow_ipc_base64.as_ref().unwrap());
    use arrow2::datatypes::DataType;
    assert_eq!(schema.fields[0].data_type(), &DataType::LargeUtf8);
    let chunk = &chunks[0];
    assert_eq!(chunk.len(), 2);
    let ids = chunk.arrays()[0]
        .as_any()
        .downcast_ref::<Utf8Array<i64>>()
        .unwrap();
    assert_eq!(ids.value(0), "a");
    assert_eq!(ids.value(1), "a");
    let marker = chunk.arrays()[2]
        .as_any()
        .downcast_ref::<BooleanArray>()
        .unwrap();
    assert_eq!(marker.values_iter().collect::<Vec<_>>(), vec![false, true]);
}

#[test]
fn ipc_omitted_when_requested_off() {
    let req = CaseBuilder::new("ipc-off", &[(1, 2)]).include_ipc().build();
    let mut req_off = req.clone();
    req_off.include_arrow_ipc = false;
    let resp = recursive_cte_backend::execute(&req_off, "itest-ipc-off").unwrap();
    assert!(resp.arrow_ipc_base64.is_none());
    let _ = req;
}
