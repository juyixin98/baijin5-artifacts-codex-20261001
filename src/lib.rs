//! # Leapfrog Triejoin backend
//!
//! Crate layout (each module has a real responsibility, see module docs):
//!
//! - [`value`] / [`schema`] / [`batch`] — typed domain values, relation schema,
//!   Arrow2 typed batches and JSON (de)serialisation of fixtures.
//! - [`trie`] — ordered Tries built per relation in a compatible variable
//!   order, with explicit row multiplicity.
//! - [`plan`] — natural-join validation, hypergraph connectivity, type/NULL
//!   policy and adaptive variable ordering.
//! - [`lftj`] — the Leapfrog Triejoin query operator with access counters.
//! - [`naive`] — independent nested-loops full-enumeration reference oracle.
//! - [`state`], [`diagnostics`], [`config`] — resources, cursor store,
//!   redacted decision records and configuration.
//! - [`validate`] — the validation/execution entry point shared by HTTP and
//!   tests.
//! - [`api`] — Axum router and handlers.

pub mod api;
pub mod batch;
pub mod config;
pub mod diagnostics;
pub mod error;
pub mod lftj;
pub mod naive;
pub mod plan;
pub mod schema;
pub mod state;
pub mod trie;
pub mod validate;
pub mod value;

pub use api::build_router;
pub use config::Config;
pub use error::{ErrorCode, ServiceError};
pub use lftj::{EngineStats, JoinEngine};
pub use naive::NaiveOracle;
pub use plan::Plan;
pub use schema::{Column, ColumnType, Relation};
pub use state::{AppState, Catalog, CursorStore};
pub use validate::{QueryOutcome, QueryRequest, ValidateDecision};
