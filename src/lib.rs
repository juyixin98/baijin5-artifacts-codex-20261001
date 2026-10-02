//! ioq-runtime: a local IO submission/completion queue teaching runtime.
//!
//! Module responsibilities:
//! - [`model`]: public data model (identities, operations, outcomes, records).
//! - [`handle`]: connection table with generation counters (连接世代).
//! - [`queue`]: bounded submission queue with explicit backpressure.
//! - [`buffer`]: buffer lease registry (resource lifecycle, double-free detection).
//! - [`adapter`]: device adapter trait plus a controllable scripted adapter.
//! - [`engine`]: the run-model state machine tying everything together.
//! - [`journal`]: persistent (JSONL) and sampled (snapshot) state.
//! - [`diag`]: diagnostic events with accept/reject/indeterminate decisions and redaction.
//! - [`api`]: Axum HTTP interface.
//! - [`config`]: runtime configuration loading.

pub mod adapter;
pub mod api;
pub mod buffer;
pub mod config;
pub mod diag;
pub mod engine;
pub mod handle;
pub mod journal;
pub mod model;
pub mod queue;
