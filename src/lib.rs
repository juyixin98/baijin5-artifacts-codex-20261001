//! # decorrelate-svc
//!
//! Decorrelation compilation and execution service for a restricted fragment
//! of correlated subqueries: `EXISTS`, `NOT EXISTS`, scalar aggregate
//! (`COUNT`/`SUM`) and bare scalar subqueries, plus NULL-aware `IN`.
//!
//! Module responsibilities:
//!
//! * [`batch`] — typed columnar batches and the Arrow2 conversion;
//! * [`operators`] — expression/aggregate/group query operators;
//! * [`resource`] — local synthetic fixture catalog (resources & state);
//! * [`validator`] — validation / name resolution / support gate (entry);
//! * [`naive`] — independent row-at-a-time reference interpreter;
//! * [`rewrite`] — decorrelated group/join executor plus applicability proofs;
//! * [`api`] — framework-free orchestration pipeline;
//! * [`server`] — Axum HTTP transport.

pub mod api;
pub mod arrow_io;
pub mod batch;
pub mod config;
pub mod error;
pub mod naive;
pub mod operators;
pub mod query;
pub mod resource;
pub mod rewrite;
pub mod server;
pub mod state;
pub mod validator;

/// Library version, re-exported for convenience.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
