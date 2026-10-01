//! # tvindex
//!
//! Column-value bitmap indexes plus SQL three-valued-logic query filtering.
//!
//! Layer map:
//! * [`index`] — typed Arrow2 batches, packed bitmaps, 3VL [`index::Tricolor`],
//!   column-value bitmap indexes and versioned deletions;
//! * [`query`] — predicate AST, binding and execution;
//! * [`state`]/[`config`] — resources, shared state and configuration;
//! * [`api`] — the Axum HTTP surface;
//! * [`verify`] — standalone validation entry point with an independent
//!   row-by-row scalar oracle.

pub mod api;
pub mod config;
pub mod error;
pub mod index;
pub mod query;
pub mod state;
pub mod verify;
