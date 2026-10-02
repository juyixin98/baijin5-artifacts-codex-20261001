//! Bound enforcement: max_depth and max_rows must return an EXPLICIT
//! incomplete status with the precise reason — never silently truncated and
//! never misreported as success.

mod common;

use common::{cross_check, run_pair, walk_tuple, CaseBuilder};
use recursive_cte_backend::plan::{
    ColumnDecl, ColumnType, Expr, IncompleteReason, JoinKey, ProjectionEntry, RecursiveTerm,
    Relation, RunLimits, RunStatus, SetQuantifier, TraversalOrder,
};
use serde_json::json;
use std::collections::BTreeMap;

/// A chain long enough that depth bounds matter: 1->2->3->4->5->6.
const CHAIN: &[(i64, i64)] = &[(1, 2), (2, 3), (3, 4), (4, 5), (5, 6)];

#[test]
fn max_depth_zero_is_a_validation_error_not_incomplete() {
    let req = CaseBuilder::new("depth-zero", CHAIN)
        .limits(0, 1000)
        .build();
    let err = run_err(&req);
    assert_eq!(
        err.category,
        recursive_cte_backend::FailureCategory::InvalidPlan
    );
    assert!(err.message.contains("max_depth"));
}

#[test]
fn max_depth_one_keeps_seed_and_depth_one_then_reports_incomplete() {
    let req = CaseBuilder::new("depth-one", CHAIN).limits(1, 1000).build();
    let pair = cross_check("depth-one", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Incomplete);
    assert_eq!(resp.incomplete_reason, Some(IncompleteReason::MaxDepth));
    let nodes: Vec<i64> = resp.output.rows.iter().map(|r| walk_tuple(r).0).collect();
    assert_eq!(nodes, vec![1, 2]);
    assert!(resp
        .log
        .iter()
        .any(|e| e.step == "limit_hit" && e.detail.contains("max_depth=1")));
}

#[test]
fn exact_depth_bound_when_graph_ends_there_is_complete() {
    // The chain ends at depth 5; max_depth=5 allows the depth-4 row to expand
    // and emit depth 5, whose row has no children -> complete.
    let req = CaseBuilder::new("depth-exact", CHAIN)
        .limits(5, 1000)
        .build();
    let pair = cross_check("depth-exact", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete, "log: {:?}", resp.log);
    assert_eq!(resp.stats.output_rows, 6);
}

#[test]
fn one_too_shallow_depth_bound_is_incomplete() {
    let req = CaseBuilder::new("depth-shallow", CHAIN)
        .limits(4, 1000)
        .build();
    let pair = cross_check("depth-shallow", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Incomplete);
    assert_eq!(resp.incomplete_reason, Some(IncompleteReason::MaxDepth));
    assert_eq!(resp.stats.output_rows, 5);
}

#[test]
fn max_depth_holds_for_dfs_as_well() {
    let req = CaseBuilder::new("depth-dfs", CHAIN)
        .dfs()
        .limits(2, 1000)
        .build();
    let pair = cross_check("depth-dfs", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Incomplete);
    assert_eq!(resp.incomplete_reason, Some(IncompleteReason::MaxDepth));
    let nodes: Vec<i64> = resp.output.rows.iter().map(|r| walk_tuple(r).0).collect();
    assert_eq!(nodes, vec![1, 2, 3]);
}

#[test]
fn set_dedup_alone_cannot_stop_depth_growing_cycle_but_bound_terminates() {
    // No cycle config AND a depth+1 column: each back-edge produces a new
    // (node, depth) pair, so global SET dedup cannot converge. This is exactly
    // what max_depth is for — the run must stop at the bound and report
    // incompleteness rather than diverging. The oracle agrees on both rows and
    // truncation.
    let edges = vec![(1, 2), (2, 1)];
    let req = CaseBuilder::new("set-only-cycle", &edges)
        .no_cycle()
        .limits(64, 1_000_000)
        .build();
    let pair = cross_check("set-only-cycle", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Incomplete);
    assert_eq!(resp.incomplete_reason, Some(IncompleteReason::MaxDepth));
    // Seed at depth 0 plus one row per depth 1..=64.
    assert_eq!(resp.stats.output_rows, 65);
    assert_eq!(resp.stats.cycles_marked, 0);
}

#[test]
fn boundary_probe_ignores_pure_duplicates_and_stays_complete() {
    // The recursive projection keeps `node` constant (child.node = R.node)
    // over a self edge, so every child has the same SET fingerprint as its
    // parent and is suppressed — including children produced by boundary rows.
    // max_depth=2 must therefore stay COMPLETE: nothing would have been added.
    let req = constant_node_request(2);
    let pair = cross_check("boundary-dup-only", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(
        resp.status,
        RunStatus::Complete,
        "pure duplicates past the bound must not trigger max_depth; log: {:?}",
        resp.log
    );
    assert_eq!(resp.incomplete_reason, None);
    assert_eq!(resp.stats.output_rows, 1);
    assert!(resp.stats.duplicates_suppressed >= 1);
}

#[test]
fn max_rows_caps_output_and_reports_incomplete() {
    let req = CaseBuilder::new("rows-cap", CHAIN).limits(64, 3).build();
    let pair = cross_check("rows-cap", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Incomplete);
    assert_eq!(resp.incomplete_reason, Some(IncompleteReason::MaxRows));
    assert_eq!(resp.stats.output_rows, 3);
    let nodes: Vec<i64> = resp.output.rows.iter().map(|r| walk_tuple(r).0).collect();
    assert_eq!(nodes, vec![1, 2, 3]);
    assert!(resp
        .log
        .iter()
        .any(|e| e.step == "limit_hit" && e.detail.contains("max_rows=3")));
}

#[test]
fn max_rows_cap_counts_every_union_all_row() {
    let edges = vec![(1, 2), (1, 2), (2, 3), (2, 3)];
    // Bag expansion yields seed + two 2s + two 3s = 5 rows; cap at 3.
    let req = CaseBuilder::new("rows-cap-all", &edges)
        .union_all()
        .limits(64, 3)
        .build();
    let pair = cross_check("rows-cap-all", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Incomplete);
    assert_eq!(resp.incomplete_reason, Some(IncompleteReason::MaxRows));
    assert_eq!(resp.stats.output_rows, 3);
}

#[test]
fn seed_larger_than_row_cap_is_incomplete_immediately() {
    let req = CaseBuilder::new("seed-cap", CHAIN)
        .seeds(&[1, 2, 3, 4])
        .limits(64, 2)
        .build();
    let pair = run_pair("seed-cap", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Incomplete);
    assert_eq!(resp.incomplete_reason, Some(IncompleteReason::MaxRows));
}

#[test]
fn complete_chain_agrees_with_oracle_on_round_counts() {
    let req = CaseBuilder::new("chain-full", CHAIN)
        .limits(64, 1000)
        .build();
    let pair = cross_check("chain-full", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete);
    // The terminal (deepest) row is still probed once, so there are six probe
    // rounds (one per materialized row); the last round emits nothing and the
    // frontier is then empty.
    assert_eq!(
        resp.stats.iterations, 6,
        "one probe round per materialized row"
    );
    assert_eq!(
        resp.stats.join_probes, 6,
        "every materialized row is probed exactly once"
    );
}

/// Build a no-cycle request whose recursive projection keeps `node` constant
/// over a self edge — all recursive children are SET duplicates.
fn constant_node_request(max_depth: usize) -> recursive_cte_backend::RecursiveRequest {
    let node = "node";
    let view = Relation {
        columns: vec![ColumnDecl {
            name: node.to_string(),
            data_type: ColumnType::Int64,
        }],
        rows: vec![vec![json!(1)]],
    };
    let edges = Relation {
        columns: vec![
            ColumnDecl {
                name: "src".to_string(),
                data_type: ColumnType::Int64,
            },
            ColumnDecl {
                name: "dst".to_string(),
                data_type: ColumnType::Int64,
            },
        ],
        rows: vec![vec![json!(1), json!(1)]],
    };
    let mut relations = BTreeMap::new();
    relations.insert("edges".to_string(), edges);

    recursive_cte_backend::RecursiveRequest {
        name: Some("boundary-dup-only".to_string()),
        view,
        set_quantifier: SetQuantifier::Union,
        relations,
        recursive_term: RecursiveTerm {
            edges_relation: "edges".to_string(),
            on: vec![JoinKey {
                recursive: node.to_string(),
                edge: "src".to_string(),
            }],
            project: vec![ProjectionEntry {
                alias: node.to_string(),
                value: Expr::RecursiveColumn {
                    name: node.to_string(),
                },
            }],
        },
        cycle: None,
        limits: RunLimits {
            max_depth,
            max_rows: 1000,
        },
        traversal_order: TraversalOrder::Bfs,
        include_log: true,
        include_arrow_ipc: false,
    }
}

fn run_err(req: &recursive_cte_backend::RecursiveRequest) -> recursive_cte_backend::EngineError {
    recursive_cte_backend::execute(req, "itest-limit-validation").unwrap_err()
}
