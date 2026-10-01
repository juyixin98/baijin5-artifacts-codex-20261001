//! `sl-resource`: resource & state management for the ordering engine.
//!
//! * [`budget::Budget`] — explicit retained-state accounting (rows and an
//!   approximate byte cost), with peak tracking used by tests to prove the
//!   streaming operator's state stays bounded instead of accumulating all
//!   input.
//! * [`spill::SpillStore`] — local Arrow IPC spill corridor used by the
//!   external-selection path when a budget is exceeded and the policy allows
//!   spilling.

pub mod budget;
pub mod spill;

pub use budget::{Budget, BudgetSnapshot, PER_ENTRY_OVERHEAD_BYTES};
pub use spill::{RunReader, RunWriter, SpillStore, SpilledRun, ROW_ID_COLUMN};
