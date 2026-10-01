//! Mutable execution state: working table, accumulated results, limits,
//! and the per-run record.

pub mod accumulated;
pub mod limits;
pub mod run;
pub mod working;

pub use accumulated::Accumulated;
pub use limits::{CompletionStatus, Limits};
pub use run::{new_run_id, RunRecord, TraceEntry, ENGINE_VERSION};
pub use working::{WorkingRow, WorkingTable};
