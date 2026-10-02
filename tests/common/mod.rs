//! Shared test fixtures and cross-check helpers.
//!
//! Every behavioral test runs the SAME request through both:
//!
//! * the production engine (`recursive_cte_backend::execute`), and
//! * the independent explicit-recursion oracle (`reference::run`),
//!
//! then compares concrete rows (as ordered multisets), counters, and the
//! terminal verdict including the precise incompleteness/failure category.
//! Case identity is carried by an explicit `run_id` and printed with the
//! engine version and the decision basis so logs are traceable.

#![allow(dead_code)]

use std::collections::BTreeMap;

use recursive_cte_backend::plan::{
    ColumnDecl, ColumnType, CycleConfig, CycleMode, Expr, JoinKey, ProjectionEntry,
    RecursiveRequest, RecursiveResponse, RecursiveTerm, Relation, RunLimits, SetQuantifier,
    TraversalOrder,
};
use recursive_cte_backend::reference::{self, ReferenceResult};
use recursive_cte_backend::{execute, EngineError, ENGINE_VERSION};
use serde_json::{json, Value};

/// Column names used by the standard graph-walk fixture.
pub const NODE: &str = "node";
pub const DEPTH: &str = "depth";
pub const PATH: &str = "path";
pub const CYCLE: &str = "is_cycle";
pub const EDGES: &str = "edges";
pub const FROM: &str = "src";
pub const TO: &str = "dst";

pub fn col(name: &str, ty: ColumnType) -> ColumnDecl {
    ColumnDecl {
        name: name.to_string(),
        data_type: ty,
    }
}

/// The standard result schema: node/depth are user columns; path/cycle are
/// engine-managed and therefore omitted from seed JSON rows.
pub fn walk_view_columns() -> Vec<ColumnDecl> {
    vec![
        col(NODE, ColumnType::Int64),
        col(DEPTH, ColumnType::Int64),
        col(PATH, ColumnType::ListInt64),
        col(CYCLE, ColumnType::Bool),
    ]
}

pub fn edge_columns() -> Vec<ColumnDecl> {
    vec![col(FROM, ColumnType::Int64), col(TO, ColumnType::Int64)]
}

pub fn seed_relation(seed_nodes: &[i64]) -> Relation {
    Relation {
        columns: vec![col(NODE, ColumnType::Int64), col(DEPTH, ColumnType::Int64)],
        rows: seed_nodes
            .iter()
            .map(|n| vec![json!(n), json!(0)])
            .collect(),
    }
}

pub fn edge_relation(pairs: &[(i64, i64)]) -> Relation {
    Relation {
        columns: edge_columns(),
        rows: pairs
            .iter()
            .map(|(s, t)| vec![json!(s), json!(t)])
            .collect(),
    }
}

pub fn cycle_config(mode: CycleMode) -> CycleConfig {
    CycleConfig {
        mode,
        key_column: NODE.to_string(),
        path_column: PATH.to_string(),
        cycle_column: CYCLE.to_string(),
    }
}

pub struct CaseBuilder {
    name: String,
    edges: Vec<(i64, i64)>,
    seeds: Vec<i64>,
    quantifier: SetQuantifier,
    order: TraversalOrder,
    max_depth: usize,
    max_rows: usize,
    cycle_mode: Option<CycleMode>,
    include_log: bool,
    include_ipc: bool,
}

impl CaseBuilder {
    pub fn new(name: &str, edges: &[(i64, i64)]) -> Self {
        Self {
            name: name.to_string(),
            edges: edges.to_vec(),
            seeds: vec![1],
            quantifier: SetQuantifier::Union,
            order: TraversalOrder::Bfs,
            max_depth: 64,
            max_rows: 10_000,
            cycle_mode: Some(CycleMode::Mark),
            include_log: true,
            include_ipc: false,
        }
    }

    pub fn seeds(mut self, seeds: &[i64]) -> Self {
        self.seeds = seeds.to_vec();
        self
    }

    pub fn union_all(mut self) -> Self {
        self.quantifier = SetQuantifier::UnionAll;
        self
    }

    pub fn dfs(mut self) -> Self {
        self.order = TraversalOrder::Dfs;
        self
    }

    pub fn limits(mut self, max_depth: usize, max_rows: usize) -> Self {
        self.max_depth = max_depth;
        self.max_rows = max_rows;
        self
    }

    pub fn cycle_mode(mut self, mode: CycleMode) -> Self {
        self.cycle_mode = Some(mode);
        self
    }

    pub fn no_cycle(mut self) -> Self {
        self.cycle_mode = None;
        self
    }

    pub fn include_ipc(mut self) -> Self {
        self.include_ipc = true;
        self
    }

    pub fn build(self) -> RecursiveRequest {
        // Seed rows carry ONLY the user columns (node, depth).
        let view = Relation {
            columns: if self.cycle_mode.is_some() {
                // The declared view has four columns; the wire seed rows
                // provide only the two non-managed ones. Use a full-width
                // column declaration, validated against two-value seed rows.
                walk_view_columns()
            } else {
                vec![col(NODE, ColumnType::Int64), col(DEPTH, ColumnType::Int64)]
            },
            rows: self
                .seeds
                .iter()
                .map(|n| vec![json!(n), json!(0)])
                .collect(),
        };

        let mut relations = BTreeMap::new();
        relations.insert(EDGES.to_string(), edge_relation(&self.edges));

        let project = vec![
            ProjectionEntry {
                alias: NODE.to_string(),
                value: Expr::EdgeColumn {
                    name: TO.to_string(),
                },
            },
            ProjectionEntry {
                alias: DEPTH.to_string(),
                value: Expr::Add {
                    left: Box::new(Expr::RecursiveColumn {
                        name: DEPTH.to_string(),
                    }),
                    right: Box::new(Expr::Literal { value: json!(1) }),
                },
            },
        ];

        RecursiveRequest {
            name: Some(self.name.clone()),
            view,
            set_quantifier: self.quantifier,
            relations,
            recursive_term: RecursiveTerm {
                edges_relation: EDGES.to_string(),
                on: vec![JoinKey {
                    recursive: NODE.to_string(),
                    edge: FROM.to_string(),
                }],
                project,
            },
            cycle: self.cycle_mode.map(cycle_config),
            limits: RunLimits {
                max_depth: self.max_depth,
                max_rows: self.max_rows,
            },
            traversal_order: self.order,
            include_log: self.include_log,
            include_arrow_ipc: self.include_ipc,
        }
    }
}

/// Result of executing one case through both implementations.
pub struct Pair {
    pub label: String,
    pub run_id: String,
    pub engine: Result<RecursiveResponse, EngineError>,
    pub oracle: ReferenceResult,
}

static CASE_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Run engine + oracle for one request and emit a correlated log line.
pub fn run_pair(label: &str, req: &RecursiveRequest) -> Pair {
    let n = CASE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let run_id = format!("itest-{label}-{n:03}");

    let engine = execute(req, &run_id);
    let oracle = reference::run(req);

    match &engine {
        Ok(resp) => println!(
            "[case {run_id}] engine_version={ENGINE_VERSION} input='{}' quantifier={:?} order={:?} \
             -> status={:?} reason={:?} rows={} cycles={} dups={} iterations={} probes={} | \
             oracle rows={} cycles={} dups={} trunc_depth={} trunc_rows={} cycle_err={}",
            req.name.as_deref().unwrap_or("?"),
            req.set_quantifier,
            req.traversal_order,
            resp.status,
            resp.incomplete_reason,
            resp.stats.output_rows,
            resp.stats.cycles_marked,
            resp.stats.duplicates_suppressed,
            resp.stats.iterations,
            resp.stats.join_probes,
            oracle.rows.len(),
            oracle.cycles_marked,
            oracle.duplicates_suppressed,
            oracle.truncated_depth,
            oracle.truncated_rows,
            oracle.cycle_error,
        ),
        Err(err) => println!(
            "[case {run_id}] engine_version={ENGINE_VERSION} input='{}' -> ERROR category={:?} msg='{}' \
             | oracle cycle_err={} trunc_depth={} trunc_rows={}",
            req.name.as_deref().unwrap_or("?"),
            err.category,
            err.message,
            oracle.cycle_error,
            oracle.truncated_depth,
            oracle.truncated_rows,
        ),
    }

    Pair {
        label: label.to_string(),
        run_id,
        engine,
        oracle,
    }
}

/// Canonical encoding of one row for multiset comparison.
pub fn row_key(row: &[Value]) -> String {
    serde_json::to_string(row).expect("row JSON serializes")
}

pub fn sorted_rows(rows: &[Vec<Value>]) -> Vec<String> {
    let mut keys = rows.iter().map(|r| row_key(r)).collect::<Vec<_>>();
    keys.sort();
    keys
}

/// Assert the engine succeeded.
pub fn assert_ok(pair: &Pair) -> &RecursiveResponse {
    match &pair.engine {
        Ok(resp) => resp,
        Err(err) => panic!(
            "case '{}' ({}) expected success but got {:?}: {}",
            pair.label, pair.run_id, err.category, err.message
        ),
    }
}

/// Compare every output row as an ordered multiset, plus counters and verdict.
pub fn assert_matches_oracle(pair: &Pair) {
    let resp = assert_ok(pair);

    let engine_rows: Vec<Vec<Value>> = resp.output.rows.clone();
    assert_eq!(
        sorted_rows(&engine_rows),
        sorted_rows(&pair.oracle.rows),
        "case '{}' ({}): row multiset mismatch\nengine: {:?}\noracle: {:?}",
        pair.label,
        pair.run_id,
        engine_rows,
        pair.oracle.rows
    );

    assert_eq!(
        resp.stats.cycles_marked, pair.oracle.cycles_marked,
        "case '{}': cycles_marked mismatch",
        pair.label
    );
    assert_eq!(
        resp.stats.duplicates_suppressed, pair.oracle.duplicates_suppressed,
        "case '{}': duplicates_suppressed mismatch",
        pair.label
    );

    match resp.incomplete_reason {
        None => {
            assert!(
                !pair.oracle.truncated_depth && !pair.oracle.truncated_rows,
                "case '{}': engine reports complete but oracle flags truncation (depth={}, rows={})",
                pair.label,
                pair.oracle.truncated_depth,
                pair.oracle.truncated_rows
            );
        }
        Some(recursive_cte_backend::plan::IncompleteReason::MaxDepth) => {
            assert!(
                pair.oracle.truncated_depth,
                "case '{}': engine reports max_depth but oracle completed",
                pair.label
            );
        }
        Some(recursive_cte_backend::plan::IncompleteReason::MaxRows) => {
            assert!(
                pair.oracle.truncated_rows,
                "case '{}': engine reports max_rows but oracle completed",
                pair.label
            );
        }
    }

    // Every log line must be tied to this run and carry the engine version.
    for entry in &resp.log {
        assert_eq!(
            entry.run_id, pair.run_id,
            "case '{}': log entry not tied to run id",
            pair.label
        );
        assert!(
            !entry.detail.is_empty(),
            "case '{}': log entry lacks a decision basis",
            pair.label
        );
    }
    assert!(
        !resp.log.is_empty(),
        "case '{}': expected progress/decision log entries",
        pair.label
    );
    assert_eq!(resp.engine_version, ENGINE_VERSION);
    assert_eq!(resp.run_id, pair.run_id);
}

/// Convenience: build + run + cross-check in one call.
pub fn cross_check(label: &str, req: &RecursiveRequest) -> Pair {
    let pair = run_pair(label, req);
    assert_matches_oracle(&pair);
    pair
}

/// Extract the (node, depth, path, cycle) tuple from a result row.
pub fn walk_tuple(row: &[Value]) -> (i64, i64, Vec<i64>, bool) {
    (
        row[0].as_i64().unwrap(),
        row[1].as_i64().unwrap(),
        row[2]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_i64().unwrap())
            .collect(),
        row[3].as_bool().unwrap(),
    )
}
