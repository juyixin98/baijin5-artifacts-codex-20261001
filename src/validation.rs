//! Validation entry point: turns untrusted request fields into a prepared,
//! executable join or returns a categorized input error. This is the single
//! boundary at which plans, batches and budgets are accepted.

use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::operator::{JoinPlan, PreparedJoin};
use crate::resource::Budget;
use crate::types::TypedBatch;

/// Lower bounds protecting against degenerate budgets. Zero would truncate
/// before producing anything and is an input error, not a resource result.
pub const MIN_PAGE_OUTPUT: usize = 1;

/// Validate the budget itself (distinct from *exceeding* a valid budget,
/// which is a resource outcome).
pub fn validate_budget(budget: &Budget) -> JoinResult<()> {
    if budget.max_output < MIN_PAGE_OUTPUT {
        return Err(JoinError::input(
            ErrorCode::InvalidPlan,
            format!(
                "max_output must be >= {MIN_PAGE_OUTPUT}, got {}",
                budget.max_output
            ),
        ));
    }
    if budget.max_candidate_accesses == 0 {
        return Err(JoinError::input(
            ErrorCode::InvalidPlan,
            "max_candidate_accesses must be >= 1",
        ));
    }
    Ok(())
}

/// Validate everything and compile the join. On success the returned
/// [`PreparedJoin`] is ready for one-shot or paged execution.
pub fn prepare(
    plan: &JoinPlan,
    left: &TypedBatch,
    right: &TypedBatch,
    budget: &Budget,
) -> JoinResult<PreparedJoin> {
    validate_budget(budget)?;
    PreparedJoin::build(plan, left, right)
}
