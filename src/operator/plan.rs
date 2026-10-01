//! Query operator descriptor: a two-predicate range join.
//!
//! A [`JoinPlan`] binds exactly two inequality predicates between two
//! typed batches. Both predicates must hold for a pair to be emitted
//! (logical AND). Each predicate names one column per side and one of
//! the four inequality directions. Exactly two predicates are required:
//! the IEJoin algorithm (and the two-sorted-permutation invariant) is
//! specific to the two-range case.

use serde::{Deserialize, Serialize};

use super::comparator::Comparator;
use crate::error::{JoinError, JoinResult};

/// One range predicate `left.column CMP right.column`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Predicate {
    pub left_column: String,
    pub op: Comparator,
    pub right_column: String,
}

impl Predicate {
    pub fn new(
        left_column: impl Into<String>,
        op: Comparator,
        right_column: impl Into<String>,
    ) -> Self {
        Self {
            left_column: left_column.into(),
            op,
            right_column: right_column.into(),
        }
    }
}

/// The full join query: two inputs, two range predicates.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct JoinPlan {
    /// Predicate 1 defines the outer scan order; predicate 2 is the
    /// inner reorder. The engine assigns them by cost (see
    /// [`JoinPlan::ordered`]); field order here is the caller's.
    pub predicates: [Predicate; 2],
}

impl JoinPlan {
    pub fn new(p1: Predicate, p2: Predicate) -> Self {
        Self {
            predicates: [p1, p2],
        }
    }

    /// Validate structural properties independent of any data:
    /// exactly two predicates, distinct left/right columns, valid
    /// comparators.
    ///
    /// # Errors
    /// `Input` category for every malformed plan.
    pub fn validate(&self) -> JoinResult<()> {
        let [p1, p2] = &self.predicates;
        if p1.left_column == p2.left_column {
            return Err(JoinError::input(
                "duplicate_predicate_column",
                "the two predicates must reference distinct left columns",
            )
            .with("left_column", serde_json::json!(p1.left_column)));
        }
        if p1.right_column == p2.right_column {
            return Err(JoinError::input(
                "duplicate_predicate_column",
                "the two predicates must reference distinct right columns",
            )
            .with("right_column", serde_json::json!(p1.right_column)));
        }
        Ok(())
    }

    /// Return the two predicates in the order the engine should process
    /// them. Predicate 1 (outer) is chosen so that its right-column
    /// values are scanned in sorted order; with two independent key
    /// columns there is no data-independent cost difference, so we keep
    /// caller order but expose this seam for testing/forcing.
    #[must_use]
    pub fn ordered(&self) -> [&Predicate; 2] {
        let [p1, p2] = &self.predicates;
        [p1, p2]
    }
}
