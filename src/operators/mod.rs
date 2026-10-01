//! Query operators: expression evaluation and the group/join build primitives
//! shared by the decorrelated executor.
//!
//! Two execution strategies live on top of these primitives:
//!
//! * [`crate::naive`] — row-at-a-time nested-loop interpretation, the
//!   independent reference used to validate rewrites;
//! * [`crate::rewrite`] — unnesting/decorrelation into group-by + outer join.
//!
//! Both consume the same resolved plan and must agree, including on errors.

pub mod agg;
pub mod groups;

use crate::batch::{Batch, Scalar};
use crate::error::{QError, QResult};
use crate::validator::{RComparison, RExpr};

/// Evaluate a resolved scalar expression against one row of a batch.
pub fn eval_expr(e: &RExpr, batch: &Batch, row: usize) -> Scalar {
    match e {
        RExpr::Null => Scalar::Null,
        RExpr::IntLit(i) => Scalar::Int(*i),
        RExpr::StrLit(s) => Scalar::Str(s.clone()),
        RExpr::Column(c) => batch.get(c.index, row).clone(),
    }
}

/// Evaluate a comparison under SQL three-valued logic:
/// `Some(true)` / `Some(false)` / `None` (UNKNOWN).
pub fn eval_comparison(c: &RComparison, batch: &Batch, row: usize) -> QResult<Option<bool>> {
    let l = eval_expr(&c.left, batch, row);
    let r = eval_expr(&c.right, batch, row);
    eval_cmp_values(c.op, &l, &r)
}

/// Compare two already-evaluated scalars with 3VL.
pub fn eval_cmp_values(op: crate::query::CmpOp, l: &Scalar, r: &Scalar) -> QResult<Option<bool>> {
    let eq = l.sql_eq(r)?;
    match eq {
        None => Ok(None),
        Some(_) => match (l, r) {
            (Scalar::Int(a), Scalar::Int(b)) => Ok(Some(op.eval(*a, *b))),
            (Scalar::Str(a), Scalar::Str(b)) => {
                // Only equality is defined on strings in this fragment.
                match op {
                    crate::query::CmpOp::Eq => Ok(Some(a == b)),
                    crate::query::CmpOp::Ne => Ok(Some(a != b)),
                    other => Err(QError::typemsg(format!(
                        "{other:?} is not defined on str values"
                    ))),
                }
            }
            _ => Err(QError::internal(
                "type-checked comparison produced non-matching values",
            )),
        },
    }
}

/// WHERE-clause acceptance: only TRUE passes; FALSE and UNKNOWN are rejected.
pub fn is_true(v: Option<bool>) -> bool {
    v == Some(true)
}
