//! Scan operator: pulls batches from a deterministic local fixture table.
//!
//! Test hooks (all explicit in the config, never hidden globals):
//! - `delay_per_batch`: blocks before each batch — used to exercise
//!   cancellation against a blocked pull. The sleep is cancellable.
//! - `fail_at_batch`: injects a `Compute` failure at a given batch index —
//!   used to verify error propagation and stream poisoning.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use arrow2::datatypes::Schema;

use crate::batch::TypedBatch;
use crate::error::QueryError;
use crate::exec::ExecutionContext;
use crate::fixtures::TableIter;

use super::{ensure_pollable, Operator, OperatorState};

#[derive(Debug, Clone)]
pub struct ScanConfig {
    pub table: String,
    pub batches: usize,
    pub batch_rows: usize,
    pub seed: u64,
    pub delay_per_batch: Duration,
    pub fail_at_batch: Option<usize>,
}

pub struct ScanOperator {
    name: String,
    state: OperatorState,
    ctx: Arc<ExecutionContext>,
    schema: Schema,
    iter: TableIter,
    delay: Duration,
    fail_at: Option<usize>,
    produced: usize,
}

impl ScanOperator {
    pub fn new(config: ScanConfig, ctx: Arc<ExecutionContext>) -> Result<Self, QueryError> {
        let iter = TableIter::new(&config.table, config.batches, config.batch_rows, config.seed)?;
        let schema = iter.schema().as_ref().clone();
        Ok(Self {
            name: format!("scan({})", config.table),
            state: OperatorState::Open,
            ctx,
            schema,
            iter,
            delay: config.delay_per_batch,
            fail_at: config.fail_at_batch,
            produced: 0,
        })
    }

    async fn poll_inner(&mut self) -> Result<Option<TypedBatch>, QueryError> {
        self.ctx.cancel.check()?;
        if self.fail_at == Some(self.produced) {
            return Err(QueryError::compute(format!(
                "injected scan failure at batch {}",
                self.produced
            )));
        }
        let Some(batch) = self.iter.next() else {
            return Ok(None);
        };
        self.produced += 1;
        if !self.delay.is_zero() {
            // Cancellable sleep: a blocked pull still observes cancellation.
            tokio::select! {
                _ = tokio::time::sleep(self.delay) => {}
                kind = self.ctx.cancel.cancelled() => {
                    return Err(QueryError::cancelled(kind));
                }
            }
        }
        Ok(Some(batch))
    }
}

#[async_trait]
impl Operator for ScanOperator {
    fn name(&self) -> &str {
        &self.name
    }

    fn schema(&self) -> &Schema {
        &self.schema
    }

    fn state(&self) -> OperatorState {
        self.state
    }

    async fn next_batch(&mut self) -> Result<Option<TypedBatch>, QueryError> {
        ensure_pollable(self.state, &self.name)?;
        let result = self.poll_inner().await;
        if result.is_err() {
            self.state = OperatorState::Errored;
        }
        result
    }

    async fn close(&mut self) -> Result<(), QueryError> {
        if self.state == OperatorState::Closed {
            return Ok(()); // idempotent
        }
        self.state = OperatorState::Closed;
        Ok(())
    }
}
