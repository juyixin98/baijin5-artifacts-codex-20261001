//! mmap-model: an in-memory page model for shared/private file mappings.
//!
//! Layers:
//! - [`model`] — the runtime: page cache, COW, sync, truncate, unmap.
//! - [`store`] — persistent-image backends (memory, filesystem, fault
//!   injection) behind the [`store::BackingStore`] trait.
//! - [`diag`] — Axum diagnostic HTTP interface.
//! - [`config`] — model/server configuration.
//! - [`error`] — stable failure categories.

pub mod config;
pub mod diag;
pub mod error;
pub mod model;
pub mod store;
pub mod telemetry;
