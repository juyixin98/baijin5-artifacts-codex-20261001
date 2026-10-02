//! # setops
//!
//! Out-of-core SQL set operations (UNION / INTERSECT / EXCEPT, DISTINCT & ALL)
//! over Arrow2 typed batches, with an Axum HTTP entry point.
//!
//! Module layout:
//! - [`error`]: the four-kind error contract (input / state / resource / compute).
//! - [`value`]: typed values and logical schema (nested NULL, list, struct).
//! - [`encoding`]: injective canonical row keys (type tags, column boundaries).
//! - [`arrow`]: typed batch <-> row conversion.
//! - [`resource`]: budgets and checked multiplicity arithmetic.
//! - [`spill`]: immutable Arrow IPC segment files and accounting.
//! - [`partition`]: buffer-bounded hash partitioning / streaming ingestion.
//! - [`executor`]: recursively split set operators over partitions.
//! - [`reference`]: independent multiset oracle used by tests.
//! - [`runlog`]: replayable run records.
//! - [`service`]: Axum app, run lifecycle and JSON HTTP contract.

pub mod arrow;
pub mod encoding;
pub mod error;
pub mod executor;
pub mod partition;
pub mod reference;
pub mod resource;
pub mod runlog;
pub mod service;
pub mod spill;
pub mod value;

pub use error::{ErrorKind, SetOpError};
pub use executor::{Quantifier, SetOperator};
