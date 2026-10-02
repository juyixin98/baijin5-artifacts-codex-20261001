//! merge-check: a local OCI-style layer merge-check backend.
//!
//! Modules, each with a distinct responsibility:
//! - [`model`]: run model — request/report/entry/diagnostic types.
//! - [`paths`]: path normalization and whiteout-marker parsing.
//! - [`merge`]: resource algorithm — the overlay merge engine.
//! - [`store`]: persistent state — JSON run records on disk.
//! - [`config`]: configuration loading and layer-root allowlist.
//! - [`api`]: diagnostics interface — the Axum HTTP API.

pub mod api;
pub mod config;
pub mod merge;
pub mod model;
pub mod paths;
pub mod store;
