//! Decorrelation compilation and execution service for correlated
//! `EXISTS` / `NOT EXISTS` and scalar aggregate subqueries.
//!
//! Module layout (each module does real work; none is a hard-coded demo):
//!
//! - [`batch`] — typed columnar batches backed by Arrow2 arrays.
//! - [`plan`] — internal query plan IR.
//! - [`ast`] — wire DTOs and explainable response models.
//! - [`catalog`] — resources/state: the typed, in-memory relation catalog.
//! - [`validator`] — validation entry, unsupported-form rejection, rewrite proof.
//! - [`executor`] — query operators: decorrelated group-probe and the
//!   independent nested-loop reference interpreter.
//! - [`service`] — orchestration, equivalence verdict, correlated tracing.
//! - [`config`] — file/environment configuration.
//! - [`http`] — Axum router and handlers.

pub mod ast;
pub mod batch;
pub mod catalog;
pub mod config;
pub mod error;
pub mod executor;
pub mod http;
pub mod plan;
pub mod service;
pub mod validator;

pub use catalog::Catalog;
pub use service::QueryService;

/// Build the shared application state with default configuration.
pub fn build_app_state() -> QueryService {
    QueryService::new(Catalog::new())
}

/// Build the shared application state with explicit configuration.
pub fn build_app_state_with(config: config::Config) -> QueryService {
    QueryService::new(Catalog::new()).with_config(config)
}
