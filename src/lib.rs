//! # pull-query
//!
//! A composable, **pull-based** query execution framework built on Arrow2.
//!
//! The crate is organized around explicit module boundaries, each with a clear
//! data and error contract:
//!
//! | Module | Responsibility |
//! |--------|----------------|
//! | [`batch`] | Typed [`batch::Batch`], [`batch::Schema`], scalar values, builders |
//! | [`error`] | The one error type and the disjoint [`error::ErrorKind`] categories |
//! | [`cancel`] | Cancellation tokens, deadlines, per-pull [`cancel::Control`] |
//! | [`diag`] | Run ids, lifecycle states, replayable structured logs |
//! | [`resource`] | Memory accounting, spill files, typed run codec |
//! | [`operator`] | The pull trait and the shared lifecycle state machine |
//! | [`operators`] | Scan, blocking spill-sort, hash join, projection, limit, faults |
//! | [`fixture`] | Local synthetic datasets (no external data/accounts) |
//! | [`oracle`] | An *independent* row-level reference implementation |
//! | [`exec`] | Plan building / execution wiring and result rendering |
//! | [`validate`] | The validation entry point exercised by tests and the server |
//!
//! ## Core guarantees
//! * Cancellation propagates cooperatively along the operator tree; already
//!   returned [`batch::Batch`]es own their data and outlive the execution.
//! * Timeout and user cancellation are distinct error kinds.
//! * Per-operator close is repeatable and releases resources exactly once.
//! * After an error a stream is poisoned: it is never pulled from again, so a
//!   damaged downstream is not consumed.
//!
//! See the repository README for error semantics and reproduction steps.

pub mod batch;
pub mod cancel;
pub mod diag;
pub mod error;
pub mod exec;
pub mod fixture;
pub mod http;
pub mod operator;
pub mod operators;
pub mod oracle;
pub mod resource;
pub mod validate;

pub use batch::{Batch, BatchBuilder, ColumnType, Scalar, Schema};
pub use cancel::{CancellationToken, Control};
pub use diag::{RunDiag, TerminalKind};
pub use error::{ErrorKind, QueryError, QueryResult};
pub use exec::{QueryPlan, QueryRequest, RunStats};
pub use operator::{drain, run_to_completion, OpState, Operator, OperatorCore};
pub use resource::ResourceTracker;
