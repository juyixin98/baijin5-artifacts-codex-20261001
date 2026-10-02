//! Tree traversal: exact rows, exact paths, and traversal-order determinism.
//!
//! Fixture (a plain rooted tree, no sharing):
//!
//! ```text
//!        1
//!       / \
//!      2   3
//!     / \
//!    4   5
//! ```
//!
//! Edges are inserted in non-sorted order on purpose to prove that output
//! ordering follows edge insertion order (and BFS/DFS configuration), not
//! accidental hash-map order.

mod common;

use common::{cross_check, run_pair, walk_tuple, CaseBuilder};
use recursive_cte_backend::plan::RunStatus;

const TREE_EDGES: &[(i64, i64)] = &[(1, 3), (2, 5), (1, 2), (2, 4)];

#[test]
fn tree_bfs_union_emits_every_node_once_with_exact_paths() {
    let req = CaseBuilder::new("tree-bfs", TREE_EDGES)
        .seeds(&[1])
        .limits(16, 1000)
        .build();

    let pair = cross_check("tree-bfs", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete);
    assert_eq!(resp.incomplete_reason, None);
    assert_eq!(resp.stats.output_rows, 5);
    assert_eq!(resp.stats.cycles_marked, 0);
    assert_eq!(resp.stats.duplicates_suppressed, 0);

    // BFS emission order is fully deterministic: frontier order then edge
    // insertion order [(1,3) before (1,2), (2,5) before (2,4)].
    let tuples: Vec<(i64, i64, Vec<i64>, bool)> =
        resp.output.rows.iter().map(|r| walk_tuple(r)).collect();
    let expected: Vec<(i64, i64, Vec<i64>, bool)> = vec![
        (1, 0, vec![1], false),
        (3, 1, vec![1, 3], false),
        (2, 1, vec![1, 2], false),
        (5, 2, vec![1, 2, 5], false),
        (4, 2, vec![1, 2, 4], false),
    ];
    assert_eq!(tuples, expected, "BFS order/path mismatch");

    // Path column must literally record the walked key sequence.
    for row in &resp.output.rows {
        let (node, depth, path, cycle) = walk_tuple(row);
        assert_eq!(*path.last().unwrap(), node, "path must end at node");
        assert_eq!(path.len() as i64, depth + 1, "path length = depth+1");
        assert!(!cycle, "tree has no cycles");
    }
}

#[test]
fn tree_dfs_has_same_rows_but_different_order() {
    let req = CaseBuilder::new("tree-dfs", TREE_EDGES)
        .seeds(&[1])
        .dfs()
        .limits(16, 1000)
        .build();

    let pair = cross_check("tree-dfs", &req);
    let resp = pair.engine.as_ref().unwrap();
    assert_eq!(resp.status, RunStatus::Complete);

    let order: Vec<i64> = resp.output.rows.iter().map(|r| walk_tuple(r).0).collect();
    // First matching edge first: from 1 we see 3 before 2 (insertion order),
    // and the stack dives down the 3 branch before the 2 branch.
    assert_eq!(order, vec![1, 3, 2, 5, 4], "DFS pre-order mismatch");

    // BFS and DFS must produce the same row MULTISET (cross-checked against
    // the oracle independently in both cases).
    let req_bfs = CaseBuilder::new("tree-bfs-cmp", TREE_EDGES)
        .seeds(&[1])
        .limits(16, 1000)
        .build();
    let pair_bfs = run_pair("tree-bfs-cmp", &req_bfs);
    let bfs_rows = common::sorted_rows(&pair_bfs.engine.as_ref().unwrap().output.rows);
    let dfs_rows = common::sorted_rows(&resp.output.rows);
    assert_eq!(bfs_rows, dfs_rows);
}

#[test]
fn tree_multiple_seeds_keep_independent_paths() {
    // Two roots: 1 (subtree 2) and 99 (isolated). Paths never bleed across
    // seed branches.
    let edges = vec![(1, 2), (2, 3)];
    let req = CaseBuilder::new("tree-multi-seed", &edges)
        .seeds(&[1, 99])
        .limits(8, 100)
        .build();

    let pair = cross_check("tree-multi-seed", &req);
    let resp = pair.engine.as_ref().unwrap();
    let mut paths: Vec<(i64, Vec<i64>)> = resp
        .output
        .rows
        .iter()
        .map(|r| {
            let t = walk_tuple(r);
            (t.0, t.2)
        })
        .collect();
    paths.sort();
    assert_eq!(
        paths,
        vec![
            (1, vec![1]),
            (2, vec![1, 2]),
            (3, vec![1, 2, 3]),
            (99, vec![99]),
        ]
    );
}

#[test]
fn tree_log_ties_every_step_to_run_and_versions_match() {
    let req = CaseBuilder::new("tree-log", TREE_EDGES)
        .limits(8, 100)
        .build();
    let pair = cross_check("tree-log", &req);
    let resp = pair.engine.as_ref().unwrap();

    assert_eq!(resp.run_id, pair.run_id);
    let steps: Vec<&str> = resp.log.iter().map(|e| e.step.as_str()).collect();
    assert!(steps.contains(&"validated"));
    assert!(steps.contains(&"seed_loaded"));
    assert!(steps.contains(&"bfs_round"));
    assert!(steps.contains(&"fixpoint"));
    assert!(steps.contains(&"finished"));
    for entry in &resp.log {
        assert_eq!(entry.run_id, pair.run_id);
        // Every step explains its decision basis.
        assert!(!entry.detail.is_empty());
    }
    // Progress evidence: the tree has 3 expansion rounds (depth 0,1,2 rows).
    let rounds: Vec<&recursive_cte_backend::plan::RunLogEntry> =
        resp.log.iter().filter(|e| e.step == "bfs_round").collect();
    assert_eq!(rounds.len(), 3, "expected 3 BFS expansion rounds");
    assert_eq!(rounds[0].frontier_rows, Some(1));
    assert_eq!(rounds[1].frontier_rows, Some(2));
    assert_eq!(rounds[2].frontier_rows, Some(2));
    // Each round reports its emitted count explicitly.
    assert_eq!(rounds[0].emitted, Some(2));
    assert_eq!(rounds[1].emitted, Some(2));
    assert_eq!(rounds[2].emitted, Some(0));
}
