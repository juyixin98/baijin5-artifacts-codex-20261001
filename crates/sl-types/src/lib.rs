//! `sl-types`: typed batches, schema/query model and scalar value semantics
//! shared by the ordering operator, resource layer, validator and server.

pub mod batch;
pub mod error;
pub mod query;
pub mod scalar;
pub mod schema;

pub use batch::{Batch, Column};
pub use error::{ErrorCategory, ErrorRecord, Result, SlError};
pub use query::{Direction, NullOrder, OrderKey, OrderQuery, OverflowPolicy};
pub use scalar::{key_equal, null_aware_cmp, value_cmp, Scalar};
pub use schema::{ColumnSchema, ColumnType, RelationSchema};

/// Crate version, surfaced by the health/info endpoint and correlated logs so
/// it is always clear which build handled a request.
pub const ENGINE_VERSION: &str = env!("CARGO_PKG_VERSION");
