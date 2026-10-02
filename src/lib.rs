//! cow-snap: copy-on-write snapshot service over a fixed page object space.
//!
//! Module boundaries and their contracts:
//! - [`config`]: static configuration, validated at startup.
//! - [`error`]: the four-category error taxonomy shared by every module
//!   (input / state conflict / resource exhausted / compute failure).
//! - [`store`]: physical page resource algorithm — allocation, refcounts,
//!   page-file IO. Knows nothing about snapshots.
//! - [`engine`]: snapshot semantics — fork, atomic write batches, delete,
//!   COW planning. Owns a [`store::PageStore`].
//! - [`persist`]: manifest checkpoint format and atomic save/load.
//! - [`diag`]: read-only diagnostic report structures (stats, verify).
//! - [`api`]: Axum HTTP boundary; translates HTTP <-> engine calls and
//!   attaches a run id to every request for log replay.

pub mod api;
pub mod config;
pub mod diag;
pub mod engine;
pub mod error;
pub mod persist;
pub mod store;
