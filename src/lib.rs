//! Restricted `WITH RECURSIVE` execution backend.
//!
//! Crate layout (one responsibility per module):
//! - [`batch`]: typed columnar batches on Arrow2
//! - [`plan`]: query operators (recursive term, union flavour, path spec)
//! - [`state`]: working table / accumulated set / limits / run record
//! - [`executor`]: the semi-naïve expansion engine
//! - [`reference`]: independent explicit-recursion oracle (test evidence)
//! - [`api`]: JSON models, validation entry point, service pipeline
//! - [`config`]: environment-driven configuration
//! - [`server`]: Axum HTTP wiring

pub mod api;
pub mod batch;
pub mod config;
pub mod error;
pub mod executor;
pub mod plan;
pub mod reference;
pub mod server;
pub mod state;
