//! ARC page replacement engine.
//!
//! Module map:
//! - [`arc`]: core replacement algorithm (T1/T2 resident lists, B1/B2 ghost
//!   lists, adaptive target `p`). Pure cache logic; all I/O is injected.
//! - [`engine`]: run model. Drives requests through the cache, assigns
//!   request ids, records diagnostics, samples state.
//! - [`store`]: backing page store abstraction + filesystem implementation.
//! - [`writeback`]: explicit write-back adapter for dirty page eviction.
//! - [`state`]: persistent snapshots and sampled state history.
//! - [`diag`]: decision records (accept / reject / undecidable) with key
//!   redaction.
//! - [`api`]: Axum HTTP interface.
//! - [`config`]: TOML configuration.

pub mod api;
pub mod arc;
pub mod config;
pub mod diag;
pub mod engine;
pub mod error;
pub mod hexutil;
pub mod state;
pub mod store;
pub mod writeback;
