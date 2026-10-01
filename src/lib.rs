//! # collate_agg
//!
//! Custom-collation string grouping/deduplication with a comparison contract between
//! a sort-based and a hash-based aggregation executor, built on Rust + axum + arrow2.
//!
//! Module responsibilities:
//! * [`batch`] — typed arrow2 columnar batches and boundary parsing;
//! * [`collation`] — rule versions, sort keys, equivalence, canonical hashing;
//! * [`exec`] — query operators: sort-agg and hash-agg grouping executors;
//! * [`oracle`] — independent reference oracle (does not reuse the core);
//! * [`service`] — the validation entry point (input -> typed verdict + diagnostics);
//! * [`state`] — resources/configuration and shared application state;
//! * [`diag`] — request-id-bearing accept/reject records and redaction;
//! * [`api`] — the axum HTTP transport.

pub mod api;
pub mod batch;
pub mod collation;
pub mod diag;
pub mod exec;
pub mod oracle;
pub mod service;
pub mod state;

pub use service::{run_comparison, run_comparison_with_id, GroupRequest, Verdict};
pub use state::{AppConfig, AppState};
