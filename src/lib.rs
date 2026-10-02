//! oci-layer-merge-checker: a local OCI-style layer merge checking backend.
//!
//! Modules:
//! * [`model`]  — the run model (what a merge run *is*).
//! * [`merge`]  — the resource algorithm (how layers combine into a tree).
//! * [`paths`]  — path normalization and isolation-root containment.
//! * [`store`]  — persistent run records (JSONL).
//! * [`diag`]   — explainable rendering of runs.
//! * [`routes`] — the HTTP diagnostic interface.
//! * [`config`] — service configuration.

pub mod config;
pub mod diag;
pub mod error;
pub mod merge;
pub mod model;
pub mod paths;
pub mod routes;
pub mod store;
