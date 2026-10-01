//! Query operators: a small SQL-shaped predicate AST and a planner that
//! evaluates it over column indexes into one [`TriSet`].
//!
//! The AST supports column predicates plus AND / OR / NOT / IS NULL.
//! Evaluation captures the table version it ran against and emits an ordered
//! trace of steps (operator, cardinality, basis) so logs can correlate a result
//! with inputs, version and the exact judgment made.

use serde::{Deserialize, Serialize};
use tracing::debug;

use crate::error::{Error, Result};
use crate::index::{CmpOp, Literal};
use crate::logic::TriSet;
use crate::state::Table;

/// Filter expression tree (JSON in API requests).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Expr {
    /// `{ "op": "cmp", "column": "age", "cmp": "gte", "value": 18 }`
    Cmp {
        column: String,
        cmp: String,
        /// Absent only for is_null / is_not_null.
        #[serde(default)]
        value: Option<Literal>,
    },
    And {
        args: Vec<Expr>,
    },
    Or {
        args: Vec<Expr>,
    },
    Not {
        arg: Box<Expr>,
    },
}

/// One evaluation trace entry: what was computed and the resulting cardinalities.
#[derive(Debug, Clone, Serialize)]
pub struct StepTrace {
    pub depth: usize,
    pub expr: String,
    pub true_rows: usize,
    pub false_rows: usize,
    pub unknown_rows: usize,
    /// Why UNKNOWN/TRUE/FALSE fell out the way they did.
    pub basis: String,
}

/// A planned, evaluated query tied to a specific table version.
#[derive(Debug)]
pub struct QueryResult {
    pub tris: TriSet,
    pub version: u64,
    pub trace: Vec<StepTrace>,
}

impl QueryResult {
    /// Row ids that passed (SQL semantics: WHERE keeps TRUE only).
    pub fn matched_ids(&self) -> Vec<usize> {
        self.tris.true_set().set_indices()
    }
}

/// Evaluate an expression against a table snapshot (single locked guard).
pub fn run(table: &Table, expr: &Expr, run_id: &str) -> Result<QueryResult> {
    let info = table.version_info();
    let alive = table.alive();
    debug!(
        run_id = %run_id,
        table = %table.name,
        version = info.version,
        total = info.total_rows,
        alive = info.alive_rows,
        deleted = info.deleted_rows,
        "query start: alive universe captured at this version"
    );
    let mut trace = Vec::new();
    let tris = eval_node(table, &alive, expr, 0, run_id, &mut trace)?;
    // Final invariant check against the captured universe.
    tris.validate()?;
    debug!(
        run_id = %run_id,
        version = info.version,
        t = tris.count_true(),
        f = tris.count_false(),
        u = tris.count_unknown(),
        "query complete: WHERE keeps TRUE; FALSE and UNKNOWN rejected"
    );
    Ok(QueryResult {
        tris,
        version: info.version,
        trace,
    })
}

fn describe(expr: &Expr) -> String {
    match expr {
        Expr::Cmp { column, cmp, value } => match value {
            Some(v) => format!("{column} {cmp} {v:?}"),
            None => format!("{column} {cmp}"),
        },
        Expr::And { .. } => "AND(...)".to_string(),
        Expr::Or { .. } => "OR(...)".to_string(),
        Expr::Not { .. } => "NOT(...)".to_string(),
    }
}

fn record(
    trace: &mut Vec<StepTrace>,
    depth: usize,
    expr: &Expr,
    tris: &TriSet,
    basis: impl Into<String>,
) {
    trace.push(StepTrace {
        depth,
        expr: describe(expr),
        true_rows: tris.count_true(),
        false_rows: tris.count_false(),
        unknown_rows: tris.count_unknown(),
        basis: basis.into(),
    });
}

#[allow(clippy::too_many_arguments)]
fn eval_node(
    table: &Table,
    alive: &crate::bits::Bitmap,
    expr: &Expr,
    depth: usize,
    run_id: &str,
    trace: &mut Vec<StepTrace>,
) -> Result<TriSet> {
    match expr {
        Expr::Cmp { column, cmp, value } => {
            let op = CmpOp::parse(cmp)?;
            if op.needs_literal() && value.is_none() {
                return Err(Error::invalid(format!(
                    "'{cmp}' on column '{column}' requires a value"
                )));
            }
            if !op.needs_literal() && value.is_some() {
                return Err(Error::invalid(format!(
                    "'{cmp}' on column '{column}' takes no value"
                )));
            }
            let index = table.indexes().get(column)?;
            let tris = index.evaluate(op, value.as_ref(), alive)?;
            let basis = format!(
                "column index over {} alive rows; NULL rows -> UNKNOWN, deleted rows excluded",
                alive.count_ones()
            );
            debug!(run_id = %run_id, step = %describe(expr), t = tris.count_true(), f = tris.count_false(), u = tris.count_unknown(), "evaluated leaf");
            record(trace, depth, expr, &tris, basis);
            Ok(tris)
        }
        Expr::Not { arg } => {
            let inner = eval_node(table, alive, arg, depth + 1, run_id, trace)?;
            // NOT swaps T/F over the ALIVE universe only; UNKNOWN fixed.
            let out = inner.negate()?;
            let basis = "NOT swaps TRUE/FALSE within alive set; UNKNOWN unchanged; never word-NOT (deleted/tail stay out)".to_string();
            debug!(run_id = %run_id, step = %describe(expr), t = out.count_true(), f = out.count_false(), u = out.count_unknown(), "applied NOT");
            record(trace, depth, expr, &out, basis);
            Ok(out)
        }
        Expr::And { args } => {
            if args.is_empty() {
                return Err(Error::invalid("AND requires at least one argument"));
            }
            let mut iter = args.iter();
            let first = eval_node(table, alive, iter.next().unwrap(), depth + 1, run_id, trace)?;
            let mut acc = first;
            for arg in iter {
                let next = eval_node(table, alive, arg, depth + 1, run_id, trace)?;
                // TriSet::and enforces identical alive sets across operands.
                acc = acc.and(&next)?;
            }
            let basis = "TRUE only if all TRUE; FALSE if any FALSE; else UNKNOWN".to_string();
            debug!(run_id = %run_id, step = %describe(expr), t = acc.count_true(), f = acc.count_false(), u = acc.count_unknown(), "applied AND");
            record(trace, depth, expr, &acc, basis);
            Ok(acc)
        }
        Expr::Or { args } => {
            if args.is_empty() {
                return Err(Error::invalid("OR requires at least one argument"));
            }
            let mut iter = args.iter();
            let first = eval_node(table, alive, iter.next().unwrap(), depth + 1, run_id, trace)?;
            let mut acc = first;
            for arg in iter {
                let next = eval_node(table, alive, arg, depth + 1, run_id, trace)?;
                acc = acc.or(&next)?;
            }
            let basis = "TRUE if any TRUE; FALSE only if all FALSE; else UNKNOWN".to_string();
            debug!(run_id = %run_id, step = %describe(expr), t = acc.count_true(), f = acc.count_false(), u = acc.count_unknown(), "applied OR");
            record(trace, depth, expr, &acc, basis);
            Ok(acc)
        }
    }
}
