//! Concrete query operators composed into pull-based trees.
//!
//! * [`scan::Scan`] / [`scan::BlockingScan`] — sources, including a slow source
//!   whose sleeps are cancel- and deadline-aware.
//! * [`projection::Projection`] — column select/reorder, fully streaming.
//! * [`limit::Limit`] — early downstream stop: asks fewer batches than upstream
//!   has, exercising the reclaim-on-partial-consumption path.
//! * [`sort::Sort`] — blocking sort with memory budget and disk spill.
//! * [`join::HashJoin`] — blocking build / streaming probe equi-join.
//! * [`failing::FailingSource`]] — fault injection for the poisoned-stream and
//!   error-category tests.
//!
//! All lifecycle behavior (error poisoning, idempotent close, cancel vs timeout)
//! lives in [`crate::operator::OperatorCore`]; these files only implement data
//! movement and [`crate::operator::Operator::release`].

pub mod failing;
pub mod join;
pub mod limit;
pub mod projection;
pub mod scan;
pub mod sort;

pub use failing::FailingSource;
pub use join::{HashJoin, JoinType};
pub use limit::Limit;
pub use projection::Projection;
pub use scan::{BlockingScan, Scan};
pub use sort::Sort;

use crate::batch::{Batch, Scalar};

/// Transpose a batch into rows of scalars (row-oriented view for sort/join).
pub fn batch_rows(batch: &Batch) -> crate::error::QueryResult<Vec<Vec<Scalar>>> {
    let cols = (0..batch.schema().len())
        .map(|c| batch.column_scalars(c))
        .collect::<crate::error::QueryResult<Vec<_>>>()?;
    let n = batch.num_rows();
    let mut rows = Vec::with_capacity(n);
    for r in 0..n {
        rows.push(cols.iter().map(|c| c[r].clone()).collect());
    }
    Ok(rows)
}

/// Build a batch from rows (inverse of [`batch_rows`]).
pub fn rows_to_batch(
    schema: std::sync::Arc<crate::batch::Schema>,
    rows: &[Vec<Scalar>],
) -> crate::error::QueryResult<Batch> {
    use crate::batch::BatchBuilder;
    let mut b = BatchBuilder::with_capacity(schema.clone(), rows.len());
    for row in rows {
        b.add_row(row)?;
    }
    b.finish()
}

/// Compare two rows on a set of key columns, ascending, nulls-first (matching
/// arrow2's default `SortOptions`). `cols` indexes into the row slices.
pub fn compare_keys(a: &[Scalar], b: &[Scalar], cols: &[usize]) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    for &c in cols {
        let ord = compare_scalar(&a[c], &b[c]);
        if ord != Ordering::Equal {
            return ord;
        }
    }
    Ordering::Equal
}

/// Nulls-first scalar ordering.
pub fn compare_scalar(a: &Scalar, b: &Scalar) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    match (a, b) {
        // Nulls sort before any value.
        (x, y) if x.is_null() && y.is_null() => Ordering::Equal,
        (x, _) if x.is_null() => Ordering::Less,
        (_, y) if y.is_null() => Ordering::Greater,
        (Scalar::Int(Some(x)), Scalar::Int(Some(y))) => x.cmp(y),
        (Scalar::Utf8(Some(x)), Scalar::Utf8(Some(y))) => x.cmp(y),
        (Scalar::Bool(Some(x)), Scalar::Bool(Some(y))) => x.cmp(y),
        // Type mismatch is not orderable; callers validate keys up front. Keep
        // total order by variant discriminant as a defensive fallback.
        _ => discr(a).cmp(&discr(b)),
    }
}

fn discr(s: &Scalar) -> u8 {
    match s {
        Scalar::Int(_) => 0,
        Scalar::Bool(_) => 1,
        Scalar::Utf8(_) => 2,
    }
}
