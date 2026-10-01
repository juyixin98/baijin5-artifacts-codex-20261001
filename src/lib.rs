//! Leapfrog Triejoin backend for three-table and restricted multi-table
//! natural joins.
//!
//! Module responsibilities:
//! * [`domain`]  — typed domain values, multiplicity, NULL policy;
//! * [`batch`]   — typed columnar batches and schemas;
//! * [`trie`]    — ordered tries + leapfrog cursors with access counters;
//! * [`query`]   — request model, validation, plan compilation;
//! * [`join`]    — LFJ engine, resume tokens, naive reference oracle;
//! * [`arrow_io`]— Arrow2 encoding (IPC stream);
//! * [`resource`]— limits, catalog, correlation ids, redacted diagnostics;
//! * [`fixtures`]— local synthetic data;
//! * [`api`]     — service pipeline and Axum HTTP boundary.
pub mod api;
pub mod arrow_io;
pub mod batch;
pub mod domain;
pub mod error;
pub mod fixtures;
pub mod join;
pub mod query;
pub mod resource;
pub mod trie;

pub use api::http::router;
pub use api::service::{run_arrow, run_json};
pub use error::{ErrorCategory, ErrorCode, JoinError, JoinResult};
pub use fixtures::build_default_catalog;
pub use query::{compile, resolve_inputs, JoinRequest, Plan, RelationInput};
pub use resource::{AppState, Catalog, ServerConfig};
