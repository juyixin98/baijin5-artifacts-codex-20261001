//! Evidence tests: tree, multi-parent diamond, self-loop, longer cycle,
//! duplicate edges — cross-checked against the independent explicit-recursion
//! oracle (`recursive_cte::reference`) and against hand-written expected
//! outputs. Every assertion pins concrete rows, paths, multiplicities and
//! failure categories — never "the endpoint responded".

mod common;

use std::collections::{BTreeMap, BTreeSet};

use common::{execute_json, id_graph_request, id_triples, int_path, run_json};
use recursive_cte::batch::Value;
use recursive_cte::config::Config;
use recursive_cte::reference::{RefGraph, RefRow};
use recursive_cte::state::CompletionStatus;

/// Identity multiset extracted from an engine response's rows.
fn engine_identities(rows: &[(i64, String, bool)]) -> BTreeSet<(i64, bool)> {
    rows.iter().map(|(id, _path, cyc)| (*id, *cyc)).collect()
}

fn engine_multiplicity(rows: &[(i64, String, bool)]) -> BTreeMap<(i64, bool), usize> {
    let mut m = BTreeMap::new();
    for (id, _path, cyc) in rows {
        *m.entry((*id, *cyc)).or_insert(0) += 1;
    }
    m
}

fn oracle_identities(rows: &[RefRow]) -> BTreeSet<(i64, bool)> {
    rows.iter()
        .map(|r| (as_int(&r.business[0]), r.cycle))
        .collect()
}

fn oracle_multiplicity(rows: &[RefRow]) -> BTreeMap<(i64, bool), usize> {
    RefGraph::multiplicity(rows)
        .into_iter()
        .map(|(mut_key, n)| (as_int(&mut_key.0[0]), mut_key.1, n))
        .map(|(id, cyc, n)| ((id, cyc), n))
        .collect()
}

fn as_int(v: &Value) -> i64 {
    match v {
        Value::Int64(n) => *n,
        other => panic!("expected int64 key, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// 1. Tree
// ---------------------------------------------------------------------------

#[test]
fn tree_distinct_bfs_matches_handwritten_rows_and_oracle() {
    let cfg = Config::default();
    let edges = [(1, 2), (1, 3), (2, 4), (2, 5)];
    let resp = run_json(id_graph_request(&[1], &edges, "DISTINCT", "bfs"), &cfg);

    assert_eq!(resp.status, CompletionStatus::Complete.as_str());
    assert!(resp.complete);

    let rows = id_triples(&resp);
    let expected = vec![
        (1, int_path(&[1]), false),
        (2, int_path(&[1, 2]), false),
        (3, int_path(&[1, 3]), false),
        (4, int_path(&[1, 2, 4]), false),
        (5, int_path(&[1, 2, 5]), false),
    ];
    assert_eq!(rows, expected, "BFS tree traversal must emit level order");

    // Independent oracle: same node/cycle identity set, no cycles in a tree.
    let oracle = RefGraph::from_key_edges([1], edges).enumerate_all(64);
    assert!(!oracle.truncated_by_depth);
    assert_eq!(engine_identities(&rows), oracle_identities(&oracle.rows));
    assert!(rows.iter().all(|(_, _, c)| !c));
}

#[test]
fn tree_dfs_preorder_matches_explicit_recursion() {
    let cfg = Config::default();
    let edges = [(1, 2), (1, 3), (2, 4), (2, 5)];
    let resp = run_json(id_graph_request(&[1], &edges, "ALL", "dfs"), &cfg);
    let rows = id_triples(&resp);

    // DFS pre-order with edges declared parent order: first child subtree first.
    assert_eq!(
        rows.iter().map(|(id, _, _)| *id).collect::<Vec<_>>(),
        vec![1, 2, 4, 5, 3]
    );

    let oracle = RefGraph::from_key_edges([1], edges).enumerate_all(64);
    let oracle_order: Vec<i64> = oracle.rows.iter().map(|r| as_int(&r.business[0])).collect();
    assert_eq!(
        rows.iter().map(|(id, _, _)| *id).collect::<Vec<_>>(),
        oracle_order,
        "DFS output order must equal explicit recursive pre-order"
    );
}

// ---------------------------------------------------------------------------
// 2. Multi-parent diamond: set semantics collapse converging walks
// ---------------------------------------------------------------------------

#[test]
fn diamond_distinct_reports_converging_node_once() {
    let cfg = Config::default();
    let edges = [(1, 2), (1, 3), (2, 4), (3, 4)];
    let resp = run_json(id_graph_request(&[1], &edges, "DISTINCT", "bfs"), &cfg);
    let rows = id_triples(&resp);

    assert_eq!(rows.len(), 4, "node 4 must be reported exactly once");
    assert_eq!(
        rows.iter().filter(|(id, _, _)| *id == 4).count(),
        1,
        "multi-parent node collapses under UNION DISTINCT"
    );
    let four = rows.iter().find(|(id, _, _)| *id == 4).unwrap();
    assert_eq!(
        four.1,
        int_path(&[1, 2, 4]),
        "BFS first arrival is via node 2"
    );
    assert!(!four.2);
    assert_eq!(
        resp.rows_deduplicated, 1,
        "the second walk to 4 is counted as a duplicate"
    );

    let oracle_distinct = RefGraph::from_key_edges([1], edges).enumerate_distinct(64);
    assert_eq!(
        engine_identities(&rows),
        oracle_identities(&oracle_distinct)
    );
}

#[test]
fn diamond_all_keeps_one_row_per_walk() {
    let cfg = Config::default();
    let edges = [(1, 2), (1, 3), (2, 4), (3, 4)];
    let resp = run_json(id_graph_request(&[1], &edges, "ALL", "bfs"), &cfg);
    let rows = id_triples(&resp);

    assert_eq!(rows.len(), 5, "both walks through node 4 survive UNION ALL");
    let paths_to_4: BTreeSet<String> = rows
        .iter()
        .filter(|(id, _, _)| *id == 4)
        .map(|(_, p, _)| p.clone())
        .collect();
    assert_eq!(
        paths_to_4,
        BTreeSet::from([int_path(&[1, 2, 4]), int_path(&[1, 3, 4])]),
        "the two walks differ by rendered path even though business rows repeat"
    );

    let oracle = RefGraph::from_key_edges([1], edges).enumerate_all(64);
    assert_eq!(
        engine_multiplicity(&rows),
        oracle_multiplicity(&oracle.rows)
    );
}

// ---------------------------------------------------------------------------
// 3. Self-loop: flagged, emitted once, never expanded => termination
// ---------------------------------------------------------------------------

#[test]
fn self_loop_is_flagged_and_does_not_inflate_under_union_all() {
    let cfg = Config::default();
    for union in ["ALL", "DISTINCT"] {
        let resp = run_json(
            id_graph_request(&[1], &[(1, 1), (1, 2)], union, "bfs"),
            &cfg,
        );
        let rows = id_triples(&resp);
        assert_eq!(
            rows,
            vec![
                (1, int_path(&[1]), false),
                (1, int_path(&[1, 1]), true),
                (2, int_path(&[1, 2]), false),
            ],
            "self-loop semantics must be identical under {union}"
        );
        assert_eq!(
            resp.status, "complete",
            "cyclic graph still reaches fixpoint"
        );
        assert!(resp.rounds < 10, "termination: no inflation from the loop");

        let oracle = RefGraph::from_key_edges([1], [(1, 1), (1, 2)]).enumerate_all(64);
        assert_eq!(
            engine_multiplicity(&rows),
            oracle_multiplicity(&oracle.rows)
        );
    }
}

// ---------------------------------------------------------------------------
// 4. Longer cycle reaching back to an ancestor
// ---------------------------------------------------------------------------

#[test]
fn two_cycle_closes_back_onto_root() {
    let cfg = Config::default();
    let edges = [(1, 2), (2, 1), (2, 3)];
    let resp = run_json(id_graph_request(&[1], &edges, "DISTINCT", "bfs"), &cfg);
    let rows = id_triples(&resp);
    assert_eq!(
        rows,
        vec![
            (1, int_path(&[1]), false),
            (2, int_path(&[1, 2]), false),
            (1, int_path(&[1, 2, 1]), true),
            (3, int_path(&[1, 2, 3]), false),
        ]
    );
    // The cycle revisit and the original visit are DISTINCT identities because
    // of the cycle marker — both must be reported.
    assert_eq!(engine_multiplicity(&rows)[&(1, false)], 1);
    assert_eq!(engine_multiplicity(&rows)[&(1, true)], 1);

    let oracle = RefGraph::from_key_edges([1], edges).enumerate_all(64);
    assert_eq!(engine_identities(&rows), oracle_identities(&oracle.rows));
}

// ---------------------------------------------------------------------------
// 5. Duplicate edges: bag vs set multiplicities
// ---------------------------------------------------------------------------

#[test]
fn duplicate_edges_yield_bag_multiplicity_under_all() {
    let cfg = Config::default();
    let edges = [(1, 2), (1, 2), (2, 3), (2, 3)];

    let all = run_json(id_graph_request(&[1], &edges, "ALL", "bfs"), &cfg);
    let all_rows = id_triples(&all);
    let mult = engine_multiplicity(&all_rows);
    assert_eq!(mult[&(1, false)], 1);
    assert_eq!(mult[&(2, false)], 2, "two parallel edges produce two walks");
    assert_eq!(
        mult[&(3, false)],
        4,
        "each duplicated walk expands independently"
    );

    let oracle = RefGraph::from_key_edges([1], edges).enumerate_all(64);
    assert_eq!(mult, oracle_multiplicity(&oracle.rows));

    let distinct = run_json(id_graph_request(&[1], &edges, "DISTINCT", "bfs"), &cfg);
    let drows = id_triples(&distinct);
    assert_eq!(drows.len(), 3);
    assert_eq!(
        distinct.rows_deduplicated, 2,
        "one duplicate 2 in round 1, one duplicate 3 in round 2"
    );
    assert!(engine_multiplicity(&drows).values().all(|n| *n == 1));
}

// ---------------------------------------------------------------------------
// 6. Cycle key is the DECLARED key, not the whole row
// ---------------------------------------------------------------------------

#[test]
fn cycle_detection_uses_declared_key_not_whole_row() {
    let cfg = Config::default();
    let req = serde_json::json!({
        "cte_name": "labeled",
        "union": "ALL",
        "order": "bfs",
        "schema": [
            {"name": "id", "type": "int64"},
            {"name": "label", "type": "utf8"}
        ],
        "key_columns": ["id"],
        "seed": [[1, "root"]],
        "edges": {
            "schema": [
                {"name": "from_id", "type": "int64"},
                {"name": "to_id", "type": "int64"},
                {"name": "to_label", "type": "utf8"}
            ],
            "rows": [
                [1, 2, "child"],
                [2, 1, "different-label-than-root"]
            ]
        },
        "recursive": {
            "parent_key": "id",
            "edge_from": "from_id",
            "projection": [
                {"edge_column": "to_id"},
                {"edge_column": "to_label"}
            ]
        }
    });
    let resp = run_json(req, &cfg);
    let rows = &resp.rows;

    let revisit = rows
        .iter()
        .find(|r| r[0].as_i64() == Some(1) && r[3].as_bool() == Some(true))
        .expect("a cycle row for id=1 must be emitted");
    assert_eq!(revisit[1].as_str(), Some("different-label-than-root"));
    assert_eq!(revisit[2].as_str(), Some("[1,2,1]"));
    // Whole-row dedup would have missed this (label differs); key-based finds it.
    assert!(
        resp.complete,
        "the flagged cycle is not re-expanded, so the run completes"
    );
}

// ---------------------------------------------------------------------------
// 7. Stable traversal order is configurable
// ---------------------------------------------------------------------------

#[test]
fn traversal_order_is_configurable_and_deterministic() {
    let cfg = Config::default();
    // Edges deliberately listed out of numeric order.
    let edges = [(1, 3), (1, 2), (2, 5), (2, 4)];

    let bfs = run_json(id_graph_request(&[1], &edges, "ALL", "bfs"), &cfg);
    assert_eq!(
        bfs.rows
            .iter()
            .map(|r| r[0].as_i64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 2, 3, 4, 5],
        "BFS sorts every frontier by the stable (business, path) key"
    );

    let input = run_json(id_graph_request(&[1], &edges, "ALL", "input"), &cfg);
    assert_eq!(
        input
            .rows
            .iter()
            .map(|r| r[0].as_i64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 3, 2, 5, 4],
        "input order preserves edge declaration order within each frontier"
    );

    let dfs = run_json(id_graph_request(&[1], &edges, "ALL", "dfs"), &cfg);
    // First edge leads to 3 (leaf); second edge subtree: 2 -> 5,4.
    assert_eq!(
        dfs.rows
            .iter()
            .map(|r| r[0].as_i64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 3, 2, 5, 4]
    );

    // Determinism: a second BFS run is byte-for-byte identical in row order.
    let bfs2 = run_json(id_graph_request(&[1], &edges, "ALL", "bfs"), &cfg);
    assert_eq!(bfs.rows, bfs2.rows);
}

// ---------------------------------------------------------------------------
// 8. Limits: explicit incomplete categories
// ---------------------------------------------------------------------------

#[test]
fn max_depth_returns_explicit_incomplete_with_limit_basis() {
    let cfg = Config::default();
    let mut req = id_graph_request(&[1], &[(1, 2), (1, 3), (2, 4), (3, 5)], "ALL", "bfs");
    req["limits"] = serde_json::json!({"max_depth": 1});

    let resp = run_json(req, &cfg);
    assert_eq!(resp.status, CompletionStatus::IncompleteMaxDepth.as_str());
    assert!(!resp.complete);
    assert_eq!(
        resp.row_count, 3,
        "rows at exactly max_depth (seed + depth-1 children) are emitted"
    );
    assert_eq!(resp.max_depth_reached, 1);
    let boundary = resp
        .trace
        .iter()
        .find(|t| t.basis.contains("not emitted"))
        .expect("trace must explain the depth boundary");
    assert_eq!(
        boundary.produced, 2,
        "children one level past max_depth are counted as blocked, not hidden"
    );
}

#[test]
fn max_rows_returns_explicit_incomplete() {
    let cfg = Config::default();
    let mut req = id_graph_request(&[1], &[(1, 2), (1, 3)], "ALL", "bfs");
    req["limits"] = serde_json::json!({"max_rows": 2});

    let resp = run_json(req, &cfg);
    assert_eq!(resp.status, CompletionStatus::IncompleteMaxRows.as_str());
    assert!(!resp.complete);
    assert_eq!(resp.row_count, 2, "exactly the ceiling number of rows");
    assert!(resp.trace.iter().any(|t| t.basis.contains("row limit")));
}

#[test]
fn limits_and_run_metadata_are_reported() {
    let cfg = Config::default();
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["limits"] = serde_json::json!({"max_depth": 3, "max_rows": 50});
    let resp = run_json(req.clone(), &cfg);
    assert_eq!(resp.max_depth, 3);
    assert_eq!(resp.max_rows, 50);
    assert_eq!(resp.engine_version, env!("CARGO_PKG_VERSION"));
    assert!(resp.run_id.starts_with("run-"));
    // Run ids correlate distinct invocations.
    let resp2 = run_json(req, &cfg);
    assert_ne!(resp.run_id, resp2.run_id, "run ids must be unique");
    // Every trace entry carries a decision basis; nothing is unclassified.
    assert!(resp.trace.iter().all(|t| !t.basis.is_empty()));
    assert!(resp
        .trace
        .iter()
        .all(|t| t.consumed + t.produced + t.admitted > 0 || t.basis.contains("seed")));
}

// ---------------------------------------------------------------------------
// 9. Arrow2 typed batch round-trip
// ---------------------------------------------------------------------------

#[test]
fn output_is_a_typed_arrow2_batch_with_aux_columns() {
    let cfg = Config::default();
    let (batch, _record) = execute_json(
        id_graph_request(&[1], &[(1, 2), (2, 1)], "DISTINCT", "bfs"),
        &cfg,
    );
    use arrow2::array::{BooleanArray, PrimitiveArray, Utf8Array};

    assert_eq!(batch.num_rows(), 3);
    let names: Vec<&str> = batch
        .schema()
        .fields()
        .iter()
        .map(|f| f.name.as_str())
        .collect();
    assert_eq!(names, vec!["id", "path", "is_cycle"]);

    let ids = batch.columns()[0]
        .as_any()
        .downcast_ref::<PrimitiveArray<i64>>()
        .unwrap();
    let paths = batch.columns()[1]
        .as_any()
        .downcast_ref::<Utf8Array<i32>>()
        .unwrap();
    let cycles = batch.columns()[2]
        .as_any()
        .downcast_ref::<BooleanArray>()
        .unwrap();

    assert_eq!(ids.value(0), 1);
    assert_eq!(paths.value(2), "[1,2,1]");
    assert!(!cycles.value(0));
    assert!(cycles.value(2));

    // The chunk exposes the Arrow2 schema too.
    let chunk = batch.to_arrow_chunk();
    assert_eq!(chunk.columns().len(), 3);
}
