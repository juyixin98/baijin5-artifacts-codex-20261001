//! Row limit / early downstream stop.
//!
//! `Limit` asks the upstream for only as many rows as required and then reports
//! end-of-stream, even though the upstream may hold many more batches. Closing
//! the limit still closes the child, reclaiming the unconsumed source's
//! buffers/handles. This is the deterministic hook for the "downstream stops
//! early → everything upstream is still reclaimed" test.

use std::sync::Arc;

use crate::batch::{Batch, Schema};
use crate::cancel::Control;
use crate::error::QueryResult;
use crate::operator::{Operator, OperatorCore};
use crate::operators::projection::Unary;

pub struct Limit {
    base: Unary,
    remaining: usize,
}

impl Limit {
    pub fn new(name: impl Into<String>, child: Box<dyn Operator>, max_rows: usize) -> Self {
        Self {
            base: Unary::new(name, child),
            remaining: max_rows,
        }
    }

    pub fn remaining(&self) -> usize {
        self.remaining
    }
}

impl Operator for Limit {
    fn name(&self) -> &str {
        self.base.core.name()
    }
    fn schema_out(&self) -> Arc<Schema> {
        self.base.child.schema_out()
    }
    fn core(&self) -> &OperatorCore {
        &self.base.core
    }
    fn core_mut(&mut self) -> &mut OperatorCore {
        &mut self.base.core
    }
    fn pull(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>> {
        if self.remaining == 0 {
            ctrl.diag()
                .info(self.name(), "limit_reached", "stopping upstream early");
            return Ok(None);
        }
        match self.base.child.next(ctrl)? {
            Some(batch) => {
                if batch.num_rows() <= self.remaining {
                    self.remaining -= batch.num_rows();
                    Ok(Some(batch))
                } else {
                    // Split a single batch down to the last needed rows.
                    let head = slice_batch(&batch, 0, self.remaining)?;
                    self.remaining = 0;
                    Ok(Some(head))
                }
            }
            None => Ok(None),
        }
    }
    fn release(&mut self, ctrl: Option<&Control>) {
        self.base.child.shutdown(ctrl);
    }
}

/// Take rows `[start, start+len)` of a batch by rebuilding from row scalars.
/// Batches are small in this framework's fixtures; clarity over a specialized
/// arrow "take" here.
fn slice_batch(batch: &Batch, start: usize, len: usize) -> QueryResult<Batch> {
    use crate::operators::{batch_rows, rows_to_batch};
    let mut rows = batch_rows(batch)?;
    rows.drain(..start);
    rows.truncate(len);
    rows_to_batch(batch.schema().clone(), &rows)
}
