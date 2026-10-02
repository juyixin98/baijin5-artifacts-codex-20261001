//! Multi-parent graphs, self loops, 2-cycles, and duplicate edges.
//!
//! These fixtures pin down the two-dedup distinction and the path-based
//! cycle marker. Every case is cross-checked against the independent oracle.

mod common;

use common::{cross_check, run_pair, walk_tuple, CaseBuilder};
use recursive_cte_backend::plan::{CycleMode, RunStatus};

/// Diamond: node 4 has two parents.
const DIAMOND: &[(i64, i64)] = &[(1, 2), (1, 3), (2, 4), (3, 4)];

#[test]
fn diamond_union_emits_shared_node_once() {
    let req = CaseBuilder::new("diamond-union", DIAMOND)
        .limits(8, 100)
        .build();
    let pair = cross_check("diamond-union", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete);
    assert_eq!(resp.stats.output_rows, 4);
    assert_eq!(resp.stats.cycles_marked, 0);
    // Exactly one arrival at 4 was suppressed (global SET dedup).
    assert_eq!(resp.stats.duplicates_suppressed, 1);

    // BFS + edge insertion order: the arrival via node 2 wins, the via-3
    // arrival is suppressed. Path-based dedup and set dedup differ here.
    let node4: Vec<(Vec<i64>, bool)> = resp
        .output
        .rows
        .iter()
        .map(|r| walk_tuple(r.as_slice()))
        .filter(|(n, _, _, _)| *n == 4)
        .map(|(_, _, p, c)| (p, c))
        .collect();
    assert_eq!(node4, vec![(vec![1, 2, 4], false)]);
}

#[test]
fn diamond_union_all_keeps_both_arrivals_with_distinct_paths() {
    let req = CaseBuilder::new("diamond-union-all", DIAMOND)
        .union_all()
        .limits(8, 100)
        .build();
    let pair = cross_check("diamond-union-all", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete);
    // 1 seed + 2,3 + two copies of 4 (one per parent path).
    assert_eq!(resp.stats.output_rows, 5);
    assert_eq!(resp.stats.duplicates_suppressed, 0);

    let mut node4_paths: Vec<Vec<i64>> = resp
        .output
        .rows
        .iter()
        .map(|r| walk_tuple(r.as_slice()))
        .filter(|(n, _, _, _)| *n == 4)
        .map(|(_, _, p, _)| p)
        .collect();
    node4_paths.sort();
    assert_eq!(
        node4_paths,
        vec![vec![1, 2, 4], vec![1, 3, 4]],
        "UNION ALL preserves both path-distinct arrivals"
    );
}

#[test]
fn self_loop_is_marked_once_and_branch_halts() {
    let edges = vec![(1, 2), (2, 2)];
    let req = CaseBuilder::new("self-loop", &edges)
        .limits(16, 100)
        .build();
    let pair = cross_check("self-loop", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete);

    let tuples: Vec<(i64, i64, Vec<i64>, bool)> = resp
        .output
        .rows
        .iter()
        .map(|r| walk_tuple(r.as_slice()))
        .collect();
    assert_eq!(
        tuples,
        vec![
            (1, 0, vec![1], false),
            (2, 1, vec![1, 2], false),
            // Revisit: declared key 2 is already on path [1,2]; marker emitted
            // with the path that *would* have been walked, then branch halts.
            (2, 2, vec![1, 2, 2], true),
        ]
    );
    assert_eq!(resp.stats.cycles_marked, 1);
}

#[test]
fn two_cycle_is_detected_on_declared_key_not_whole_row() {
    // 2 <-> 3: without declared-key path membership, the growing depth/path
    // columns would make every row unique and the walk would run forever.
    let edges = vec![(1, 2), (2, 3), (3, 2)];
    let req = CaseBuilder::new("two-cycle", &edges)
        .limits(16, 100)
        .build();
    let pair = cross_check("two-cycle", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete);

    let last = resp.output.rows.last().map(|r| walk_tuple(r));
    assert_eq!(last, Some((2, 3, vec![1, 2, 3, 2], true)));
    assert_eq!(resp.stats.cycles_marked, 1);
    // No fixpoint blow-up: bounded output.
    assert_eq!(resp.stats.output_rows, 4);
}

#[test]
fn duplicate_edges_union_all_preserve_multiplicity() {
    let edges = vec![(1, 2), (1, 2), (2, 3)];
    let req = CaseBuilder::new("dup-edges-all", &edges)
        .union_all()
        .limits(8, 100)
        .build();
    let pair = cross_check("dup-edges-all", &req);
    let resp = pair.engine.as_ref().unwrap();

    let nodes: Vec<i64> = resp.output.rows.iter().map(|r| walk_tuple(r).0).collect();
    // Seed once; node 2 produced by both parallel edges; each of the two node-2
    // rows independently reaches node 3.
    assert_eq!(nodes, vec![1, 2, 2, 3, 3]);
    assert_eq!(resp.stats.duplicates_suppressed, 0);
}

#[test]
fn duplicate_edges_union_collapse_multiplicity() {
    let edges = vec![(1, 2), (1, 2), (2, 3), (2, 3)];
    let req = CaseBuilder::new("dup-edges-union", &edges)
        .limits(8, 100)
        .build();
    let pair = cross_check("dup-edges-union", &req);
    let resp = pair.engine.as_ref().unwrap();

    let nodes: Vec<i64> = resp.output.rows.iter().map(|r| walk_tuple(r).0).collect();
    assert_eq!(nodes, vec![1, 2, 3]);
    // Two duplicate arrivals suppressed (one for node 2, one for node 3).
    assert_eq!(resp.stats.duplicates_suppressed, 2);
}

#[test]
fn duplicate_self_loop_edges_union_all_emit_two_markers_then_halt() {
    // Both path membership (always on) and bag multiplicity (UNION ALL) are
    // exercised together: two parallel self edges yield two marker rows but
    // neither branch is re-expanded, so termination still holds.
    let edges = vec![(1, 2), (2, 2), (2, 2)];
    let req = CaseBuilder::new("dup-self-loop-all", &edges)
        .union_all()
        .limits(16, 100)
        .build();
    let pair = cross_check("dup-self-loop-all", &req);
    let resp = pair.engine.as_ref().unwrap();

    let markers: Vec<(i64, bool)> = resp
        .output
        .rows
        .iter()
        .map(|r| walk_tuple(r.as_slice()))
        .map(|(n, _, _, c)| (n, c))
        .collect();
    assert_eq!(markers, vec![(1, false), (2, false), (2, true), (2, true)]);
    assert_eq!(resp.stats.output_rows, 4);
}

#[test]
fn cycle_mode_error_fails_with_category_instead_of_marker() {
    let edges = vec![(1, 2), (2, 2)];
    let req = CaseBuilder::new("self-loop-error", &edges)
        .cycle_mode(CycleMode::Error)
        .limits(16, 100)
        .build();
    let pair = run_pair("self-loop-error", &req);

    let err = pair
        .engine
        .as_ref()
        .expect_err("cycle.mode=error must fail, not succeed quietly");
    assert_eq!(
        err.category,
        recursive_cte_backend::FailureCategory::InvalidData,
        "concrete failure category required"
    );
    assert!(
        err.message.contains("cycle detected"),
        "message must explain the failure: {}",
        err.message
    );
    assert!(
        pair.oracle.cycle_error,
        "oracle must agree a cycle occurred"
    );
}

#[test]
fn dfs_and_bfs_agree_on_cyclic_graph_multiset() {
    let edges = vec![(1, 2), (2, 3), (3, 2), (1, 3)];
    let bfs = CaseBuilder::new("cyc-bfs", &edges).limits(16, 100).build();
    let dfs = CaseBuilder::new("cyc-dfs", &edges)
        .dfs()
        .limits(16, 100)
        .build();

    let pb = cross_check("cyc-bfs", &bfs);
    let pd = cross_check("cyc-dfs", &dfs);
    assert_eq!(
        common::sorted_rows(&pb.engine.as_ref().unwrap().output.rows),
        common::sorted_rows(&pd.engine.as_ref().unwrap().output.rows)
    );
}
