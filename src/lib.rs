//! # groupagg
//!
//! Backend operators for exact grouped `PERCENTILE_CONT` / `PERCENTILE_DISC`,
//! `MODE() WITHIN GROUP` and ordered string aggregation, built on Arrow2 typed
//! batches with bounded-memory external sorting, cooperative cancellation and
//! resumable merge state.
//!
//! Module responsibilities:
//! * [`batch`] — Arrow2-backed typed columnar batches and their JSON decoding
//! * [`plan`] — logical query plan plus pre-execution quantile validation
//! * [`exec`] — query operators: ingest/external sort, streaming finalizers,
//!   memory budget, cancellation and spill state
//! * [`fixtures`] — deterministic local synthetic datasets
//! * [`config`] — startup configuration (TOML/env)
//! * [`api`] — the Axum validation/serving entry point
//! * [`diagnostics`] — request-scoped, redacting diagnostic records

pub mod batch;
pub mod config;
pub mod diagnostics;
pub mod error;
pub mod exec;
pub mod fixtures;
pub mod plan;
pub mod server;

/// The HTTP API module, re-exported under the interface-oriented name.
pub use server as api;

pub use batch::{Batch, Column, DataType, Field};
pub use error::{Error, ErrorKind, Result};
pub use exec::{Engine, EngineConfig, GroupResult, QueryResult};
pub use plan::{AggDef, AggOp, Plan, SortOrder};
