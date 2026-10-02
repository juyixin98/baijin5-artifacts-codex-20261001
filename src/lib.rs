//! procdiff — process resource diff service over local synthetic /proc snapshots.
//!
//! Module responsibilities:
//! - [`config`]: service configuration (sampling and classification knobs).
//! - [`model`]: run model — process identity (boot generation aware), snapshots.
//! - [`snapshot`]: filesystem loader for synthetic /proc snapshot directories.
//! - [`delta`]: resource algorithm — counter delta classification
//!   (monotonic / wraparound / reset / anomaly).
//! - [`engine`]: sampling state machine that ingests snapshots, tracks
//!   generations, re-parenting, partial reads and exits.
//! - [`tree`]: process tree reconstruction with parent history timelines.
//! - [`store`]: persistence of sampling state (JSON, atomic rename).
//! - [`diag`]: diagnostic records with request ids and redacted key state.
//! - [`api`]: Axum diagnostic HTTP interface.

pub mod api;
pub mod config;
pub mod delta;
pub mod diag;
pub mod engine;
pub mod model;
pub mod snapshot;
pub mod store;
pub mod tree;
