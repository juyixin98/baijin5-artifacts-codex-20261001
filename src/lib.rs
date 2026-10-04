//! cdc-service: content-defined chunking service.
//!
//! Modules with real responsibilities:
//! - `gear`        — deterministic rolling-hash table
//! - `chunker`     — streaming CDC core (min/max/target-pattern)
//! - `manifest`    — algorithm + parameter identity
//! - `format`      — CDCB/1 binary container encode/decode
//! - `recovery`    — verification & recovery kernel
//! - `limits`      — resource control
//! - `diagnostics` — request ids and decision logging (redacted)
//! - `config`      — startup configuration
//! - `api`         — axum HTTP surface

pub mod api;
pub mod chunker;
pub mod config;
pub mod diagnostics;
pub mod error;
pub mod format;
pub mod gear;
pub mod limits;
pub mod manifest;
pub mod recovery;
