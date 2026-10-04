//! cdc-service: rolling-hash content-defined chunking with verifiable
//! manifests.
//!
//! Module map:
//! - [`gear`] — rolling hash primitive (spec-stable).
//! - [`params`] — chunking parameters and validation.
//! - [`chunker`] — streaming content-defined chunker.
//! - [`manifest`] — self-describing chunk manifest.
//! - [`format`] — deterministic binary manifest encoding ("CDCM").
//! - [`recovery`] — reassembly and byte-authoritative verification.
//! - [`limits`] — resource control.
//! - [`config`] — startup configuration.
//! - [`diagnostics`] — decision records with redaction.
//! - [`api`] — HTTP surface.

pub mod analysis;
pub mod api;
pub mod chunker;
pub mod config;
pub mod diagnostics;
pub mod format;
pub mod gear;
pub mod limits;
pub mod manifest;
pub mod params;
pub mod recovery;
