//! # tribool-index
//!
//! Column-value bitmap indexes with SQL three-valued-logic filtering
//! (AND / OR / NOT / IS NULL) over Arrow2 typed batches, with versioned row
//! deletes and an Axum HTTP API.
//!
//! Module map:
//! * [`bits`] — word-packed bitmaps with tail-padding forced to zero;
//! * [`logic`] — SQL 3VL TRUE/FALSE/UNKNOWN partition of one alive universe;
//! * [`batch`] — typed, null-aware column batches backed by Arrow2 arrays;
//! * [`index`] — per-column value bitmap indexes and predicate evaluation;
//! * [`state`] — table resources and versioned deletes;
//! * [`query`] — filter AST and query operators with evaluation traces;
//! * [`config`] — layered configuration (defaults / TOML / env);
//! * [`api`] — Axum routes and typed error responses.

pub mod api;
pub mod batch;
pub mod bits;
pub mod config;
pub mod error;
pub mod index;
pub mod logic;
pub mod query;
pub mod state;

pub use error::{Error, ErrorKind, Result};
