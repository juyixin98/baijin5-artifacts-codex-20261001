//! Fault-injection source used by the error-category and poisoned-stream tests.
//!
//! It yields a configurable number of good batches and then fails with a chosen
//! [`ErrorKind`] on a chosen pull. Tests use the good batches emitted *before*
//! the failure to prove returned data stays readable, and the repeated `next`
//! calls afterwards to prove the stream is poisoned (same error, no re-entry).

use std::sync::Arc;

use crate::batch::{Batch, Schema};
use crate::cancel::Control;
use crate::error::{ErrorKind, QueryError, QueryResult};
use crate::operator::{Operator, OperatorCore};

pub struct FailingSource {
    core: OperatorCore,
    schema: Arc<Schema>,
    good: Vec<Batch>,
    /// Zero-based pull on which to fail (after emitting that many good batches).
    fail_on_pull: u64,
    fail_kind: ErrorKind,
    fail_message: String,
    /// Counts how many times the internal `pull` actually ran, proving that
    /// after poisoning the framework never re-enters the operator.
    pub reentry_guard: u64,
}

impl FailingSource {
    pub fn new(
        name: impl Into<String>,
        schema: Arc<Schema>,
        good: Vec<Batch>,
        fail_kind: ErrorKind,
        fail_message: impl Into<String>,
    ) -> Self {
        // Fail on the pull just after the last good batch by default.
        let fail_on_pull = good.len() as u64;
        Self {
            core: OperatorCore::new(name),
            schema,
            good,
            fail_on_pull,
            fail_kind,
            fail_message: fail_message.into(),
            reentry_guard: 0,
        }
    }

    /// Fail on an explicit zero-based pull index instead of the default.
    pub fn fail_on_pull(mut self, pull: u64) -> Self {
        self.fail_on_pull = pull;
        self
    }
}

impl Operator for FailingSource {
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

    fn pull(&mut self, _ctrl: &Control) -> QueryResult<Option<Batch>> {
        self.reentry_guard += 1;
        // The framework increments its pull counter before invoking us, so the
        // zero-based index of this concrete pull is `pulls - 1`.
        let idx = self.core.pulls() - 1;
        if idx < self.fail_on_pull && (idx as usize) < self.good.len() {
            return Ok(Some(self.good[idx as usize].clone()));
        }
        let e = match self.fail_kind {
            ErrorKind::InvalidInput => QueryError::invalid_input(&self.fail_message),
            ErrorKind::StateConflict => QueryError::state_conflict(&self.fail_message),
            ErrorKind::ResourceExhausted => QueryError::resource_exhausted(&self.fail_message),
            ErrorKind::ComputationFailed => QueryError::computation(&self.fail_message),
            ErrorKind::Timeout => QueryError::timeout(&self.fail_message),
            ErrorKind::Cancelled => QueryError::cancelled(&self.fail_message),
        };
        Err(e)
    }
}
