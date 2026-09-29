//! IEJoin-style two-predicate range join backend.
//!
//! Crate layout and data/error contracts:
//! * [`types`] — Arrow2-backed typed batches (`TypedBatch`, `Column`, `Scalar`);
//! * [`permutation`] — stable sort permutations, inverse position maps and the
//!   strict/non-strict equal-group boundaries;
//! * [`operator`] — the query plan, the IEJoin core, the bitmap primitive and
//!   the independent nested-loop reference;
//! * [`resource`] — budgets, stats and the truncation marker;
//! * [`state`] — resumable paging sessions and cursors;
//! * [`replay`] — run-id records (fingerprints, checkpoints, rationale);
//! * [`validation`] — the single entry point that accepts untrusted plans;
//! * [`api`] — the Axum HTTP boundary (only layer that knows HTTP).
//!
//! Every fallible boundary returns [`error::JoinError`] whose
//! [`error::ErrorCode`] falls into one of four distinguishable categories:
//! input validation, state conflict, resource exhaustion, computation failure.

pub mod error;
pub mod operator;
pub mod permutation;
pub mod replay;
pub mod resource;
pub mod state;
pub mod types;
pub mod validation;

pub mod api;

pub use error::{ErrorCategory, ErrorCode, JoinError, JoinResult};
pub use operator::{join_full, Checkpoint, JoinPage, OutputPair, PreparedJoin};
pub use operator::{Comparator, JoinPlan, Predicate};
pub use resource::{Budget, JoinStats, Truncation};
pub use types::{Column, KeyType, Scalar, TypedBatch};
