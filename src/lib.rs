//! rbp-column: hybrid RLE / bit-pack block codec for u64 integer columns.
//!
//! Module map:
//!   * [`format`]  — binary format definition (header, packing primitives)
//!   * [`encode`]  — encoder kernel (run segmentation + block emission)
//!   * [`decode`]  — decoder kernel (budget-validated, located errors)
//!   * [`error`]   — error taxonomy with failure categories
//!   * [`config`]  — configuration layer (TOML + env overrides)
//!   * [`server`]  — Axum interop service (encode/decode HTTP API)

pub mod config;
pub mod decode;
pub mod encode;
pub mod error;
pub mod format;
pub mod server;
