//! Resource limits and state/resource accounting.
//!
//! Budgets are explicit and checked at system boundaries so that
//! oversized requests fail with the `ResourceExhausted` category rather
//! than panicking or exhausting memory. The same structure is shared by
//! the one-shot API and the cursor-based batch API.

use serde::{Deserialize, Serialize};

use crate::error::{JoinError, JoinResult};

/// Resource policy for one join execution.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Budget {
    /// Maximum number of output pairs materialized per response.
    pub max_output_pairs: u64,
    /// Maximum number of rows accepted on either input side.
    pub max_input_rows: usize,
    /// Maximum number of key columns referenced (fixed by IEJoin at 2).
    pub max_predicates: usize,
    /// Maximum batches a cursor may yield before it must be renewed.
    pub max_batches_per_cursor: u64,
}

impl Default for Budget {
    fn default() -> Self {
        Self {
            max_output_pairs: 100_000,
            max_input_rows: 1_000_000,
            max_predicates: 2,
            max_batches_per_cursor: 10_000,
        }
    }
}

impl Budget {
    /// Check input cardinalities against the budget before planning.
    ///
    /// # Errors
    /// `ResourceExhausted` when either side exceeds `max_input_rows`.
    pub fn check_input(&self, left_rows: usize, right_rows: usize) -> JoinResult<()> {
        if left_rows > self.max_input_rows {
            return Err(JoinError::exhausted(
                "input_too_large",
                format!(
                    "left side has {left_rows} rows, limit is {}",
                    self.max_input_rows
                ),
            )
            .with("rows", serde_json::json!(left_rows))
            .with("limit", serde_json::json!(self.max_input_rows))
            .with("side", serde_json::json!("left")));
        }
        if right_rows > self.max_input_rows {
            return Err(JoinError::exhausted(
                "input_too_large",
                format!(
                    "right side has {right_rows} rows, limit is {}",
                    self.max_input_rows
                ),
            )
            .with("rows", serde_json::json!(right_rows))
            .with("limit", serde_json::json!(self.max_input_rows))
            .with("side", serde_json::json!("right")));
        }
        Ok(())
    }

    /// Enforce the output-pair cap at the point of materialization.
    ///
    /// # Errors
    /// `ResourceExhausted` when `current` already reached the cap.
    pub fn check_output_capacity(&self, current: u64) -> JoinResult<()> {
        if current >= self.max_output_pairs {
            return Err(JoinError::exhausted(
                "output_budget_reached",
                format!("output pair budget of {} reached", self.max_output_pairs),
            )
            .with("emitted", serde_json::json!(current))
            .with("limit", serde_json::json!(self.max_output_pairs)));
        }
        Ok(())
    }
}
