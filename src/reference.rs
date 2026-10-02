//! Independent reference oracle.
//!
//! This module answers the same questions as the production engine using a
//! completely separate implementation:
//!
//! * its own JSON-valued scalar representation (no `batch::Scalar`, no Arrow2),
//! * its own tiny expression evaluator,
//! * an explicit recursive DFS walk carrying the branch path explicitly,
//! * set semantics via canonical-JSON fingerprints.
//!
//! It shares ONLY the wire request model with the engine, so a green
//! cross-check means the engine agrees with an independently derived answer —
//! the expected results are never produced by the code under test.

use std::collections::BTreeSet;

use serde_json::Value;

use crate::plan::{
    CycleMode, Expr, JoinKey, ProjectionEntry, RecursiveRequest, SetQuantifier, TraversalOrder,
};

/// The oracle's verdict for one request.
#[derive(Debug, Clone)]
pub struct ReferenceResult {
    /// Full-width rows in declared view-column order (managed columns filled).
    /// Order is the oracle's native DFS order; compare as multisets/sets.
    pub rows: Vec<Vec<Value>>,
    pub cycles_marked: usize,
    pub duplicates_suppressed: usize,
    /// True if `max_depth` cut off a descendant that would have been emitted.
    pub truncated_depth: bool,
    /// True if `max_rows` cut off emission.
    pub truncated_rows: bool,
    /// True if `cycle.mode = error` was triggered.
    pub cycle_error: bool,
}

/// Managed column names bundled for quick checks.
#[derive(Debug, Clone)]
struct Managed {
    key: String,
    path: String,
    cycle: String,
}

/// Outcome of considering one matched candidate edge.
enum EdgeDecision {
    /// The edge does not match the join predicate.
    NoMatch,
    /// A fresh child was admitted and should be traversed.
    Admitted(Vec<Value>),
    /// The candidate terminated its branch (cycle marker emitted, duplicate
    /// suppressed, or beyond the depth bound) without error.
    Halted,
    /// Processing must stop globally (cycle error / row cap reached).
    Stop,
}

struct Oracle<'a> {
    req: &'a RecursiveRequest,
    edges: &'a [Vec<Value>],
    edge_cols: &'a [String],
    view_cols: Vec<String>,
    managed: Option<Managed>,
    /// User (non-managed) view columns, in view order.
    #[allow(dead_code)]
    user_cols: Vec<String>,
    /// Index of each view column in the narrow seed rows.
    seed_pos: Vec<Option<usize>>,
    project: Vec<&'a ProjectionEntry>,
    on: &'a [JoinKey],
    seen: BTreeSet<String>,
    rows: Vec<Vec<Value>>,
    cycles: usize,
    duplicates: usize,
    truncated_depth: bool,
    truncated_rows: bool,
    cycle_error: bool,
}

/// Run the oracle. Deterministic; never panics on validated requests.
pub fn run(req: &RecursiveRequest) -> ReferenceResult {
    let edges = &req
        .relations
        .get(&req.recursive_term.edges_relation)
        .expect("validated edge relation")
        .rows;
    let edge_cols: Vec<String> = req
        .relations
        .get(&req.recursive_term.edges_relation)
        .expect("validated edge relation")
        .columns
        .iter()
        .map(|c| c.name.clone())
        .collect();

    let managed = req.cycle.as_ref().map(|c| Managed {
        key: c.key_column.clone(),
        path: c.path_column.clone(),
        cycle: c.cycle_column.clone(),
    });
    let view_cols: Vec<String> = req.view.columns.iter().map(|c| c.name.clone()).collect();
    let user_cols: Vec<String> = req
        .view
        .columns
        .iter()
        .filter(|c| Some(&c.name) != managed.as_ref().map(|m| &m.path))
        .filter(|c| Some(&c.name) != managed.as_ref().map(|m| &m.cycle))
        .map(|c| c.name.clone())
        .collect();

    // Seed rows are given in user-column order.
    let seed_pos: Vec<Option<usize>> = view_cols
        .iter()
        .map(|name| user_cols.iter().position(|u| u == name))
        .collect();

    let project = req.recursive_term.project.iter().collect::<Vec<_>>();

    let mut o = Oracle {
        req,
        edges,
        edge_cols: &edge_cols,
        view_cols,
        managed,
        user_cols,
        seed_pos,
        project,
        on: &req.recursive_term.on,
        seen: BTreeSet::new(),
        rows: Vec::new(),
        cycles: 0,
        duplicates: 0,
        truncated_depth: false,
        truncated_rows: false,
        cycle_error: false,
    };

    let seeds = req.view.rows.clone();
    for narrow in &seeds {
        let mut full = vec![Value::Null; o.view_cols.len()];
        for (view_i, pos) in o.seed_pos.iter().enumerate() {
            if let Some(p) = pos {
                full[view_i] = narrow[*p].clone();
            }
        }
        if let Some(m) = &o.managed {
            let key = column(&full, &o.view_cols, &m.key).clone();
            put_column(&mut full, &o.view_cols, &m.path, Value::Array(vec![key]));
            put_column(&mut full, &o.view_cols, &m.cycle, Value::Bool(false));
        }
        o.emit(full, false);
    }

    // Re-walk the admitted seed rows using the configured traversal order.
    // Both walks are implemented independently here (JSON scalars, own eval),
    // but their first-arrival order matches the engine so that cycle-marker
    // rows — which embed path and depth — are identical across implementations.
    let seed_rows = o.rows.clone();
    match req.traversal_order {
        TraversalOrder::Bfs => o.walk_bfs(seed_rows),
        TraversalOrder::Dfs => {
            for row in seed_rows {
                let path = o
                    .managed
                    .as_ref()
                    .map(|m| as_path(column(&row, &o.view_cols, &m.path)));
                o.visit_dfs(row, 0, path);
                if o.truncated_rows || o.cycle_error {
                    break;
                }
            }
        }
    }

    ReferenceResult {
        rows: o.rows,
        cycles_marked: o.cycles,
        duplicates_suppressed: o.duplicates,
        truncated_depth: o.truncated_depth,
        truncated_rows: o.truncated_rows,
        cycle_error: o.cycle_error,
    }
}

impl<'a> Oracle<'a> {
    fn is_union(&self) -> bool {
        self.req.set_quantifier == SetQuantifier::Union
    }

    /// Canonical fingerprint over the SET columns: every user column plus the
    /// cycle marker, excluding the path.
    fn fingerprint(&self, full: &[Value]) -> String {
        let mut parts: Vec<String> = Vec::with_capacity(full.len());
        for (i, name) in self.view_cols.iter().enumerate() {
            if Some(name) == self.managed.as_ref().map(|m| &m.path) {
                continue;
            }
            parts.push(format!("{i}:{}", canonical(&full[i])));
        }
        parts.join("|")
    }

    /// Emit a row honoring the quantifier. `is_cycle_row` selects the marker
    /// column value already embedded in `full`.
    fn emit(&mut self, full: Vec<Value>, is_cycle_row: bool) -> bool {
        if self.is_union() {
            let fp = self.fingerprint(&full);
            if !self.seen.insert(fp) {
                self.duplicates += 1;
                return false;
            }
        }
        if self.rows.len() >= self.req.limits.max_rows {
            self.truncated_rows = true;
            return false;
        }
        self.rows.push(full);
        if is_cycle_row {
            self.cycles += 1;
        }
        true
    }

    /// Decision produced by considering one candidate edge.
    fn handle_edge(
        &mut self,
        parent: &[Value],
        edge: usize,
        depth: usize,
        path: &Option<Vec<Value>>,
    ) -> EdgeDecision {
        if !self.join_matches(parent, edge) {
            return EdgeDecision::NoMatch;
        }
        let edge_row = self.edges[edge].clone();

        // Project the child in full view width.
        let mut child = vec![Value::Null; self.view_cols.len()];
        for entry in &self.project {
            let value = self.eval(&entry.value, parent, &edge_row);
            let idx = self
                .view_cols
                .iter()
                .position(|c| c == &entry.alias)
                .expect("validated alias");
            child[idx] = value;
        }
        if let Some(m) = &self.managed {
            let key = column(&child, &self.view_cols, &m.key).clone();
            let mut next_path = path.clone().unwrap_or_default();
            next_path.push(key.clone());
            put_column(
                &mut child,
                &self.view_cols,
                &m.path,
                Value::Array(next_path),
            );
            put_column(&mut child, &self.view_cols, &m.cycle, Value::Bool(false));

            let is_cycle = path
                .as_ref()
                .is_some_and(|p| p.iter().any(|k| json_eq(k, &key)));
            let beyond_bound = depth + 1 > self.req.limits.max_depth;
            if is_cycle {
                match self
                    .req
                    .cycle
                    .as_ref()
                    .map(|c| c.mode)
                    .unwrap_or(CycleMode::Mark)
                {
                    CycleMode::Error => {
                        if beyond_bound {
                            self.truncated_depth = true;
                        } else {
                            self.cycle_error = true;
                        }
                        return EdgeDecision::Stop;
                    }
                    CycleMode::Mark => {
                        let mut marker = child.clone();
                        put_column(&mut marker, &self.view_cols, &m.cycle, Value::Bool(true));
                        if beyond_bound {
                            if self.would_emit(&marker) {
                                self.truncated_depth = true;
                            }
                        } else {
                            self.emit(marker, true);
                        }
                        // The branch halts here: nothing admitted, no error.
                        return EdgeDecision::Halted;
                    }
                }
            }
        }

        // A fresh child beyond the bound proves incompleteness ONLY when set
        // semantics would actually have emitted it; a suppressed duplicate
        // changes nothing, matching the engine's boundary probe.
        if depth + 1 > self.req.limits.max_depth {
            let would_emit_fresh = !self.is_union() || {
                let fp = self.fingerprint(&child);
                !self.seen.contains(&fp)
            };
            if would_emit_fresh {
                self.truncated_depth = true;
            }
            return EdgeDecision::Halted;
        }

        let admitted = self.emit(child.clone(), false);
        if admitted {
            EdgeDecision::Admitted(child)
        } else {
            // Suppressed by UNION or by the row cap. The cap sets its own flag
            // inside emit; either way the branch is not re-expanded.
            if self.truncated_rows {
                EdgeDecision::Stop
            } else {
                EdgeDecision::Halted
            }
        }
    }

    /// Level-synchronous BFS walk mirroring the engine's round processing:
    /// one FIFO frontier, edges considered in insertion order, fresh children
    /// appended as the next round (global set dedup applies immediately).
    fn walk_bfs(&mut self, seeds: Vec<Vec<Value>>) {
        let mut frontier: Vec<(Vec<Value>, usize, Option<Vec<Value>>)> = seeds
            .into_iter()
            .map(|row| {
                let path = self
                    .managed
                    .as_ref()
                    .map(|m| as_path(column(&row, &self.view_cols, &m.path)));
                (row, 0, path)
            })
            .collect();

        while !frontier.is_empty() && !self.truncated_rows && !self.cycle_error {
            let mut next_round = Vec::new();
            for (parent, depth, path) in frontier.drain(..) {
                for edge in 0..self.edges.len() {
                    match self.handle_edge(&parent, edge, depth, &path) {
                        EdgeDecision::NoMatch | EdgeDecision::Halted => {}
                        EdgeDecision::Admitted(child) => {
                            let next_path = self
                                .managed
                                .as_ref()
                                .map(|m| as_path(column(&child, &self.view_cols, &m.path)));
                            next_round.push((child, depth + 1, next_path));
                        }
                        EdgeDecision::Stop => return,
                    }
                }
            }
            frontier = next_round;
        }
    }

    /// Explicit recursive DFS: edges considered in insertion order and each
    /// fresh child is dived into immediately (pre-order), matching the engine
    /// stack's first-match-first discipline.
    fn visit_dfs(&mut self, parent: Vec<Value>, depth: usize, path: Option<Vec<Value>>) {
        if self.truncated_rows || self.cycle_error {
            return;
        }
        for edge in 0..self.edges.len() {
            if self.truncated_rows || self.cycle_error {
                return;
            }
            match self.handle_edge(&parent, edge, depth, &path) {
                EdgeDecision::NoMatch | EdgeDecision::Halted => {}
                EdgeDecision::Stop => return,
                EdgeDecision::Admitted(child) => {
                    let next_path = self
                        .managed
                        .as_ref()
                        .map(|m| as_path(column(&child, &self.view_cols, &m.path)));
                    self.visit_dfs(child, depth + 1, next_path);
                }
            }
        }
    }

    /// Whether `emit` would currently keep the row (set-policy aware).
    fn would_emit(&self, full: &[Value]) -> bool {
        if !self.is_union() {
            return true;
        }
        let fp = self.fingerprint(full);
        !self.seen.contains(&fp)
    }

    fn join_matches(&self, parent: &[Value], edge_idx: usize) -> bool {
        let edge_row = &self.edges[edge_idx];
        self.on.iter().all(|k| {
            let rv = column(parent, &self.view_cols, &k.recursive);
            let ev = &edge_row[self
                .edge_cols
                .iter()
                .position(|c| c == &k.edge)
                .expect("validated edge column")];
            // SQL semantics: null never joins.
            !rv.is_null() && !ev.is_null() && json_eq(rv, ev)
        })
    }

    /// Independent evaluator for the restricted expression subset.
    fn eval(&self, expr: &Expr, rec_row: &[Value], edge_row: &[Value]) -> Value {
        match expr {
            Expr::RecursiveColumn { name } => column(rec_row, &self.view_cols, name).clone(),
            Expr::EdgeColumn { name } => edge_row[self
                .edge_cols
                .iter()
                .position(|c| c == name)
                .expect("validated edge column")]
            .clone(),
            Expr::Literal { value } => value.clone(),
            Expr::Add { left, right } => arith(
                self.eval(left, rec_row, edge_row),
                self.eval(right, rec_row, edge_row),
                i64::checked_add,
            ),
            Expr::Sub { left, right } => arith(
                self.eval(left, rec_row, edge_row),
                self.eval(right, rec_row, edge_row),
                i64::checked_sub,
            ),
        }
    }
}

fn arith(a: Value, b: Value, op: fn(i64, i64) -> Option<i64>) -> Value {
    match (a, b) {
        (Value::Null, _) | (_, Value::Null) => Value::Null,
        (Value::Number(x), Value::Number(y)) => {
            let (xi, yi) = (x.as_i64().unwrap_or(0), y.as_i64().unwrap_or(0));
            op(xi, yi).map(Value::from).unwrap_or(Value::Null)
        }
        _ => Value::Null,
    }
}

fn column<'r>(row: &'r [Value], cols: &[String], name: &str) -> &'r Value {
    let idx = cols.iter().position(|c| c == name).expect("known column");
    &row[idx]
}

fn put_column(row: &mut [Value], cols: &[String], name: &str, value: Value) {
    let idx = cols.iter().position(|c| c == name).expect("known column");
    row[idx] = value;
}

fn as_path(v: &Value) -> Vec<Value> {
    match v {
        Value::Array(xs) => xs.clone(),
        _ => Vec::new(),
    }
}

/// JSON equality that is type-strict (1 != true, as the typed engine treats
/// int64 and bool as distinct scalar types).
fn json_eq(a: &Value, b: &Value) -> bool {
    canonical(a) == canonical(b)
}

/// Canonical, type-discriminated string for a scalar JSON value.
fn canonical(v: &Value) -> String {
    match v {
        Value::Null => "n:".to_string(),
        Value::Bool(b) => format!("b:{b}"),
        Value::Number(n) => format!("i:{}", n.as_i64().unwrap_or(0)),
        Value::String(s) => format!("s:{s}"),
        Value::Array(xs) => {
            let inner = xs.iter().map(canonical).collect::<Vec<_>>().join(",");
            format!("a:[{inner}]")
        }
        Value::Object(_) => "o:{}".to_string(),
    }
}
