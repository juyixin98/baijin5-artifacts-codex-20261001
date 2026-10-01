//! set_ops: typed batched UNION / INTERSECT / EXCEPT (DISTINCT & ALL)
//! with partitioned external-memory execution on Arrow2 batches.
//!
//! Crate layout (data & error contracts live with each module):
//! - [`batch`]: typed batches, canonical row encoding, fixture CSV IO, decoding
//! - [`operator`]: query operators, in-memory and spilling partition execution
//! - [`resource`]: resource limits and accounting
//! - [`runlog`]: replayable run ids / event logs
//! - [`service`]: Axum HTTP validation/execution entrypoint

pub mod batch;
pub mod error;
pub mod operator;
pub mod resource;
pub mod runlog;
pub mod service;
pub mod spill;

pub use batch::{Schema, TypedBatch};
pub use error::{ErrorContext, ErrorKind, InputCode, ResourceCode, Result, SetOpsError, StateCode};
pub use operator::{ExecutionMode, Qualifier, Query, SetOp, SetOpOutput, SetOpStats, execute};
pub use resource::ResourceLimits;
