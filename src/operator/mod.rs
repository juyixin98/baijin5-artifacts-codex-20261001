//! Pull-based operator contract.
//!
//! Operators are composed into a tree; consumers pull batches via
//! [`Operator::next_batch`]. The protocol:
//!
//! - `next_batch` returns `Ok(Some(batch))`, `Ok(None)` at end-of-stream, or
//!   `Err` on failure.
//! - **An error poisons the stream.** After any `Err`, the operator moves to
//!   `Errored` and every further `next_batch` returns
//!   `StateConflict("poll after error")` — a corrupted downstream stream can
//!   never be consumed further.
//! - `close` is idempotent and releases all operator-held resources
//!   (reservations, spill files, child operators). Polling after `close` is
//!   a `StateConflict`, closing twice is `Ok(())` and never double-frees:
//!   resources live in drop-guards, and `close` just drops them early.

pub mod join;
pub mod scan;
pub mod sort;

use async_trait::async_trait;
use arrow2::datatypes::Schema;

use crate::batch::TypedBatch;
use crate::error::QueryError;

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum OperatorState {
    Open,
    Errored,
    Closed,
}

#[async_trait]
pub trait Operator: Send {
    fn name(&self) -> &str;
    fn schema(&self) -> &Schema;
    fn state(&self) -> OperatorState;
    /// Pull the next batch. See module docs for the error-poisoning contract.
    async fn next_batch(&mut self) -> Result<Option<TypedBatch>, QueryError>;
    /// Idempotent shutdown. Cascades to child operators.
    async fn close(&mut self) -> Result<(), QueryError>;
}

/// Gate at the top of every `next_batch` implementation.
pub(crate) fn ensure_pollable(state: OperatorState, name: &str) -> Result<(), QueryError> {
    match state {
        OperatorState::Open => Ok(()),
        OperatorState::Closed => Err(QueryError::state_conflict(format!(
            "operator '{name}' polled after close"
        ))),
        OperatorState::Errored => Err(QueryError::state_conflict(format!(
            "operator '{name}' polled after a fatal error; the stream is poisoned"
        ))),
    }
}
