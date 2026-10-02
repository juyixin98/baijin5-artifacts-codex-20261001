//! pullq — a composable pull-based query execution framework.
//!
//! Module map (data and error contracts live at these boundaries):
//! - [`batch`]     — typed record batches, the data contract between operators
//! - [`error`]     — the error taxonomy every module speaks
//! - [`cancel`]    — cancellation token shared across an operator tree
//! - [`resources`] — memory budget, spill files, tracked tasks (per query)
//! - [`exec`]      — execution context: run id, deadline, cancellation
//! - [`operator`]  — the pull-based operator contract + scan/sort/join
//! - [`plan`]      — plan description, validation entry, tree construction
//! - [`fixtures`]  — deterministic synthetic tables (all data is local)
//! - [`service`]   — axum HTTP entry

pub mod batch;
pub mod cancel;
pub mod error;
pub mod exec;
pub mod fixtures;
pub mod operator;
pub mod plan;
pub mod resources;
pub mod service;
