//! Teaching runtime for local IO submission/completion queues with
//! cancellation and connection generations.
//!
//! Module map:
//! - [`model`] — core data model (handles, generations, records)
//! - [`ring`] — fixed-capacity slot table (the submission queue)
//! - [`runtime`] — the run model enforcing the business contracts
//! - [`resources`] — buffer pool (the resource algorithm)
//! - [`adapter`] — backend boundary (scripted fixture + real filesystem)
//! - [`journal`] — persistent, sampled completion state
//! - [`diag`] — diagnostics/demo HTTP interface with redaction
//! - [`config`] — TOML configuration

pub mod adapter;
pub mod config;
pub mod diag;
pub mod journal;
pub mod model;
pub mod resources;
pub mod ring;
pub mod runtime;
