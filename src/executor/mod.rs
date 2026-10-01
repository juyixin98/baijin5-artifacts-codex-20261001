//! Recursive-term execution: the semi-naïve expansion loop.
//!
//! Three strategies share one expansion primitive and differ only in how the
//! working set is drained and ordered:
//!
//! - [`TraversalOrder::BreadthFirst`]: whole frontier per round, each frontier
//!   sorted by `(business tuple, key path)` for stable output.
//! - [`TraversalOrder::InputOrder`]: whole frontier per round in edge-input
//!   arrival order, no sorting.
//! - [`TraversalOrder::DepthFirst`]: LIFO stack, one row per step.
//!
//! Cycle rows are emitted with their flag set but never expanded, so a cyclic
//! graph always terminates even under `UNION ALL`.

use std::cmp::Ordering;

use crate::batch::{RecordBatch, Value};
use crate::error::{EngineError, Result};
use crate::plan::{RecursivePlan, ResolvedExpr, TraversalOrder, UnionOp};
use crate::state::accumulated::AccumulatedIdentity;
use crate::state::{
    Accumulated, CompletionStatus, Limits, RunRecord, TraceEntry, WorkingRow, WorkingTable,
};

/// Indexed join/projection context resolved once per run.
struct ExpansionCtx<'a> {
    plan: &'a RecursivePlan,
    edge_from_idx: usize,
    parent_key_idx: usize,
    key_indices: Vec<usize>,
    projection: Vec<ResolvedExpr>,
    edge_rows: Vec<Vec<Value>>,
}

impl<'a> ExpansionCtx<'a> {
    fn build(plan: &'a RecursivePlan) -> Result<Self> {
        let edge_from_idx = plan
            .recursive
            .edges
            .schema()
            .require_index(&plan.recursive.edge_from)?;
        let parent_key_idx = plan.parent_key_index()?;
        let key_indices = plan.key_indices()?;
        if key_indices.is_empty() {
            return Err(EngineError::validation(
                "cycle marking requires at least one key column",
            ));
        }
        let projection = plan.resolve_projection()?;
        let edge_rows = plan.recursive.edges.to_rows();
        Ok(Self {
            plan,
            edge_from_idx,
            parent_key_idx,
            key_indices,
            projection,
            edge_rows,
        })
    }

    /// Key tuple of a business row under the declared key columns.
    fn key_of(&self, business: &[Value]) -> Vec<Value> {
        self.key_indices
            .iter()
            .map(|&i| business[i].clone())
            .collect()
    }

    /// One join+projection expansion of `parent`. Returns every child row
    /// (including cycle rows and, under `UNION ALL`, duplicates).
    fn expand_one(&self, parent: &WorkingRow) -> Vec<WorkingRow> {
        let parent_key = &parent.business[self.parent_key_idx];
        let mut children = Vec::new();
        // Edge scan in declared input order; callers decide ordering policy.
        for edge_row in &self.edge_rows {
            if &edge_row[self.edge_from_idx] != parent_key {
                continue;
            }
            let business: Vec<Value> = self
                .projection
                .iter()
                .map(|expr| match expr {
                    ResolvedExpr::EdgeColumn(i) => edge_row[*i].clone(),
                    ResolvedExpr::Literal(v) => v.clone(),
                })
                .collect();
            let child_key = self.key_of(&business);
            // Cycle test reads ONLY declared keys of ancestors — never the
            // rendered path column, which grows forever.
            let cycle = parent.path.iter().any(|ancestor| ancestor == &child_key);
            let mut path = parent.path.clone();
            path.push(child_key);
            children.push(WorkingRow {
                business,
                path,
                cycle,
                depth: parent.depth + 1,
            });
        }
        children
    }
}

/// Compare value slices lexicographically using the stable scalar order.
fn compare_slices(a: &[Value], b: &[Value]) -> Ordering {
    for (x, y) in a.iter().zip(b.iter()) {
        match x.stable_cmp(y) {
            Ordering::Equal => continue,
            non_eq => return non_eq,
        }
    }
    a.len().cmp(&b.len())
}

/// Full sort key of a working row: business tuple then flattened key path.
fn sort_key(row: &WorkingRow) -> (&[Value], Vec<Value>) {
    let flat: Vec<Value> = row.path.iter().flatten().cloned().collect();
    (&row.business, flat)
}

fn stable_row_cmp(a: &WorkingRow, b: &WorkingRow) -> Ordering {
    let (ab, ap) = sort_key(a);
    let (bb, bp) = sort_key(b);
    compare_slices(ab, bb).then_with(|| compare_slices(&ap, &bp))
}

/// Outcome of admitting one round of candidates.
struct Admission {
    admitted: Vec<WorkingRow>,
    duplicates: usize,
    /// Rows actually appended to output (cycles included; duplicates excluded).
    emitted: Vec<WorkingRow>,
}

/// Apply set dedup and cycle policy to freshly produced candidates,
/// accumulating accepted rows. Stops early (with the limit status) once the
/// row ceiling is reached.
fn admit(
    candidates: Vec<WorkingRow>,
    accumulated: &mut Accumulated,
    distinct: bool,
    limits: Limits,
    status: &mut CompletionStatus,
) -> Admission {
    let mut out = Admission {
        admitted: Vec::new(),
        duplicates: 0,
        emitted: Vec::new(),
    };
    for child in candidates {
        if distinct && accumulated.contains(&child) {
            out.duplicates += 1;
            continue;
        }
        if (accumulated.len() as u64) >= limits.max_rows {
            *status = CompletionStatus::IncompleteMaxRows;
            break;
        }
        let inserted = accumulated.insert(&child, distinct);
        if !inserted {
            out.duplicates += 1;
            continue;
        }
        out.emitted.push(child.clone());
        // Cycle rows are reported but never expanded again.
        if !child.cycle {
            out.admitted.push(child);
        }
    }
    out
}

/// Execute a validated plan to completion (or to an explicit limit verdict).
pub fn execute(plan: &RecursivePlan, limits: Limits) -> Result<(RecordBatch, RunRecord)> {
    let ctx = ExpansionCtx::build(plan)?;
    let distinct = plan.union_op == UnionOp::Distinct;

    let mut record = RunRecord::start(&plan.name, plan.union_op, limits.max_depth, limits.max_rows);
    let mut accumulated = Accumulated::new();
    let mut status = CompletionStatus::Complete;

    // ---- Seed rows (depth 0) ----
    let mut seeds: Vec<WorkingRow> = plan
        .seed
        .to_rows()
        .into_iter()
        .map(|business| {
            let key = ctx.key_of(&business);
            WorkingRow::seed(business, key)
        })
        .collect();
    if matches!(plan.traversal, TraversalOrder::BreadthFirst) {
        seeds.sort_by(stable_row_cmp);
    }

    let mut working = WorkingTable::new();

    // ---- Expansion loop ----
    match plan.traversal {
        TraversalOrder::DepthFirst => run_dfs(
            &ctx,
            seeds,
            &mut working,
            &mut accumulated,
            limits,
            distinct,
            &mut record,
            &mut status,
        ),
        TraversalOrder::BreadthFirst | TraversalOrder::InputOrder => run_frontier(
            &ctx,
            seeds,
            &mut working,
            &mut accumulated,
            limits,
            distinct,
            &mut record,
            &mut status,
        ),
    }?;

    record.status = status;
    record.rows_emitted = accumulated.len();

    let output = RecordBatch::from_rows(plan.output_schema(), accumulated.output_rows().to_vec())
        .map_err(|e| {
        EngineError::internal(format!("failed to materialize output batch: {e}"))
    })?;
    Ok((output, record))
}

/// Frontier loop shared by BFS and input-order traversal.
#[allow(clippy::too_many_arguments)]
fn run_frontier(
    ctx: &ExpansionCtx<'_>,
    seeds: Vec<WorkingRow>,
    working: &mut WorkingTable,
    accumulated: &mut Accumulated,
    limits: Limits,
    distinct: bool,
    record: &mut RunRecord,
    status: &mut CompletionStatus,
) -> Result<()> {
    let sort = matches!(ctx.plan.traversal, TraversalOrder::BreadthFirst);

    // Depth-0 round: seeds are admitted before any join runs.
    let seed_admission = admit(seeds, accumulated, distinct, limits, status);
    record.rows_deduplicated += seed_admission.duplicates;
    record.max_depth_reached = 0;
    record.trace.push(TraceEntry {
        round: 0,
        depth: 0,
        consumed: 0,
        produced: seed_admission.emitted.len(),
        cycles: 0,
        duplicates: seed_admission.duplicates,
        admitted: seed_admission.admitted.len(),
        basis: "seed rows admitted".to_owned(),
    });
    record.rounds = 1;
    working.set_next(seed_admission.admitted);

    while !working.is_empty() && status.is_complete() {
        let round = record.rounds;
        let depth = working.rows().first().map(|r| r.depth).unwrap_or(0) + 1;
        let parents = working.drain();
        let consumed = parents.len();

        // The join always runs: rows at depth == max_depth are emitted, rows
        // beyond it are only counted as blocked.
        let mut candidates = Vec::new();
        let mut blocked_by_depth = 0usize;
        for parent in &parents {
            for child in ctx.expand_one(parent) {
                if child.depth > limits.max_depth {
                    blocked_by_depth += 1;
                } else {
                    candidates.push(child);
                }
            }
        }
        if blocked_by_depth > 0 {
            *status = CompletionStatus::IncompleteMaxDepth;
        }
        if sort {
            candidates.sort_by(stable_row_cmp);
        }
        let produced = candidates.len() + blocked_by_depth;
        let cycles = candidates.iter().filter(|c| c.cycle).count();
        let admission = admit(candidates, accumulated, distinct, limits, status);
        record.rows_deduplicated += admission.duplicates;
        if !admission.emitted.is_empty() {
            record.max_depth_reached = depth;
        }
        record.trace.push(TraceEntry {
            round,
            depth,
            consumed,
            produced,
            cycles,
            duplicates: admission.duplicates,
            admitted: admission.admitted.len(),
            basis: if blocked_by_depth > 0 {
                format!(
                    "max_depth={} reached; {blocked_by_depth} child row(s) not emitted",
                    limits.max_depth
                )
            } else {
                trace_basis(status, admission.admitted.len())
            },
        });
        working.set_next(admission.admitted);
        record.rounds += 1;
    }
    Ok(())
}

/// Explicit LIFO stack loop matching pre-order explicit recursion.
///
/// Dedup is applied **at push time** (`UNION DISTINCT`), rows are emitted
/// **at pop time**, so the output order is the recursive pre-order even
/// though set semantics globally suppress repeats.
#[allow(clippy::too_many_arguments)]
fn run_dfs(
    ctx: &ExpansionCtx<'_>,
    seeds: Vec<WorkingRow>,
    working: &mut WorkingTable,
    accumulated: &mut Accumulated,
    limits: Limits,
    distinct: bool,
    record: &mut RunRecord,
    status: &mut CompletionStatus,
) -> Result<()> {
    // Identities pushed but not yet emitted, so converging siblings under
    // `UNION DISTINCT` are stacked only once.
    let mut scheduled: std::collections::HashSet<AccumulatedIdentity> =
        std::collections::HashSet::new();

    // Push seeds in reverse so the first declared seed pops first.
    for seed in seeds.into_iter().rev() {
        if distinct && !scheduled.insert(Accumulated::identity(&seed)) {
            record.rows_deduplicated += 1;
            continue;
        }
        working.push(seed);
    }

    while let Some(parent) = working.pop() {
        // The row ceiling is a hard stop the instant it trips. Depth cutoff,
        // by contrast, only suppresses over-deep children; in-limit rows
        // already stacked (other branches) are still emitted.
        if matches!(*status, CompletionStatus::IncompleteMaxRows) {
            break;
        }
        let round = record.rounds;

        // Emit at pop time; the row ceiling is enforced here.
        if (accumulated.len() as u64) >= limits.max_rows {
            *status = CompletionStatus::IncompleteMaxRows;
            record.trace.push(TraceEntry {
                round,
                depth: parent.depth,
                consumed: 1,
                produced: 0,
                cycles: 0,
                duplicates: 0,
                admitted: 0,
                basis: "row limit hit; stacked rows not emitted".to_owned(),
            });
            record.rounds += 1;
            break;
        }
        accumulated.insert(&parent, distinct);

        let depth = parent.depth;
        record.max_depth_reached = record.max_depth_reached.max(depth);

        // Cycle rows are emitted (above) but never expanded.
        if parent.cycle {
            record.trace.push(TraceEntry {
                round,
                depth,
                consumed: 1,
                produced: 0,
                cycles: 1,
                duplicates: 0,
                admitted: 0,
                basis: "cycle row emitted; not expanded".to_owned(),
            });
            record.rounds += 1;
            continue;
        }

        // Expand the parent. Children beyond max_depth are never stacked
        // (rows at exactly max_depth were already emitted on pop above; their
        // children sit one level too deep and are counted as blocked).
        let candidates = ctx.expand_one(&parent);
        let mut produced = 0usize;
        let mut blocked_by_depth = 0usize;
        let mut cycles = 0usize;
        let mut to_push: Vec<WorkingRow> = Vec::new();
        let mut duplicates = 0usize;
        for child in candidates {
            produced += 1;
            if child.cycle {
                cycles += 1;
            }
            if child.depth > limits.max_depth {
                blocked_by_depth += 1;
                continue;
            }
            let identity = Accumulated::identity(&child);
            if distinct && (accumulated.contains_identity(&identity) || !scheduled.insert(identity))
            {
                duplicates += 1;
                continue;
            }
            to_push.push(child);
        }
        if blocked_by_depth > 0 {
            *status = CompletionStatus::IncompleteMaxDepth;
        }
        let admitted = to_push.len();
        record.rows_deduplicated += duplicates;
        record.trace.push(TraceEntry {
            round,
            depth,
            consumed: 1,
            produced,
            cycles,
            duplicates,
            admitted,
            basis: if blocked_by_depth > 0 {
                format!(
                    "max_depth={} reached; {blocked_by_depth} child row(s) not emitted",
                    limits.max_depth
                )
            } else {
                trace_basis(status, admitted)
            },
        });
        // First edge match pops first: reverse edge-order children onto stack.
        for child in to_push.into_iter().rev() {
            working.push(child);
        }
        record.rounds += 1;
    }
    Ok(())
}

fn trace_basis(status: &CompletionStatus, admitted: usize) -> String {
    match status {
        CompletionStatus::IncompleteMaxRows => {
            "row limit hit while admitting candidates".to_owned()
        }
        CompletionStatus::IncompleteMaxDepth => "depth limit reached".to_owned(),
        CompletionStatus::Complete if admitted == 0 => {
            "no new rows admitted; fixpoint approaching".to_owned()
        }
        CompletionStatus::Complete => "candidates admitted for next round".to_owned(),
    }
}
