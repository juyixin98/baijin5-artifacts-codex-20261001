//! Source operators. [`Scan`] replays in-memory fixture batches; the more
//! interesting [`BlockingScan`] sleeps a cancel- and deadline-aware amount
//! before producing each batch, which is how timeout and cancellation races are
//! driven deterministically.

use std::sync::Arc;
use std::time::Duration;

use crate::batch::{Batch, Schema};
use crate::cancel::Control;
use crate::error::QueryResult;
use crate::operator::{Operator, OperatorCore};

/// Replays a fixed list of batches. Owns its data outright, so a consumer
/// holding an already-pulled batch is unaffected when the scan is closed.
pub struct Scan {
    core: OperatorCore,
    schema: Arc<Schema>,
    batches: Vec<Batch>,
    cursor: usize,
}

impl Scan {
    pub fn new(name: impl Into<String>, schema: Arc<Schema>, batches: Vec<Batch>) -> Self {
        Self {
            core: OperatorCore::new(name),
            schema,
            batches,
            cursor: 0,
        }
    }
}

impl Operator for Scan {
    fn name(&self) -> &str {
        self.core.name()
    }
    fn schema_out(&self) -> Arc<Schema> {
        self.schema.clone()
    }
    fn core(&self) -> &OperatorCore {
        &self.core
    }
    fn core_mut(&mut self) -> &mut OperatorCore {
        &mut self.core
    }
    fn pull(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>> {
        if self.cursor < self.batches.len() {
            let i = self.cursor;
            self.cursor += 1;
            ctrl.diag().info(
                self.core.name(),
                "emitting",
                format!("batch {i} rows={}", self.batches[i].num_rows()),
            );
            Ok(Some(self.batches[i].clone()))
        } else {
            Ok(None)
        }
    }
}

/// A slow source: before each emitted batch it sleeps in cancel/deadline-aware
/// slices. Also supports an optional artificial delay before the *first* batch.
///
/// `batches` are stored once and cheaply cloned per pull (Arrow arrays are
/// reference-counted), so the returned batch still outlives the scan.
pub struct BlockingScan {
    core: OperatorCore,
    schema: Arc<Schema>,
    batches: Vec<Batch>,
    cursor: usize,
    per_batch_delay: Duration,
    initial_delay: Duration,
}

impl BlockingScan {
    pub fn new(
        name: impl Into<String>,
        schema: Arc<Schema>,
        batches: Vec<Batch>,
        per_batch_delay: Duration,
    ) -> Self {
        Self {
            core: OperatorCore::new(name),
            schema,
            batches,
            cursor: 0,
            per_batch_delay,
            initial_delay: Duration::ZERO,
        }
    }

    /// Add a one-time delay before the first emitted batch.
    pub fn with_initial_delay(mut self, d: Duration) -> Self {
        self.initial_delay = d;
        self
    }
}

impl Operator for BlockingScan {
    fn name(&self) -> &str {
        self.core.name()
    }
    fn schema_out(&self) -> Arc<Schema> {
        self.schema.clone()
    }
    fn core(&self) -> &OperatorCore {
        &self.core
    }
    fn core_mut(&mut self) -> &mut OperatorCore {
        &mut self.core
    }
    fn pull(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>> {
        if self.cursor >= self.batches.len() {
            return Ok(None);
        }
        let delay = if self.cursor == 0 {
            self.initial_delay + self.per_batch_delay
        } else {
            self.per_batch_delay
        };
        if delay > Duration::ZERO {
            ctrl.diag().info(
                self.core.name(),
                "blocked",
                format!("sleeping {delay:?} before batch {}", self.cursor),
            );
            // The sleep is the safe point: cancel and deadline are both checked
            // every 5ms inside, so either control signal wins promptly.
            ctrl.interruptible_sleep(delay)?;
        }
        let i = self.cursor;
        self.cursor += 1;
        Ok(Some(self.batches[i].clone()))
    }
}
