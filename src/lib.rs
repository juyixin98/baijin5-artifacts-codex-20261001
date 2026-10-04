//! iblt-service: a teaching service for set-difference reconciliation
//! built on Invertible Bloom Lookup Tables (IBLT).
//!
//! Module map and data contracts:
//!
//! - [`hash`]: deterministic keyed hashing (index hashes + key checksum).
//! - [`iblt`]: the encode/recover kernel. Cells carry `(count, key_sum,
//!   hash_sum)`; `subtract` produces a difference table where negative
//!   counts mark the reverse difference; `decode` peels only cells whose
//!   checksum verifies.
//! - [`format`]: the versioned binary wire format for tables
//!   (see `docs/protocol.md`).
//! - [`limits`]: resource-control knobs enforced at every boundary.
//! - [`error`]: the single error taxonomy shared by all modules
//!   (input / state-conflict / resource / computation).
//! - [`runlog`]: run-id allocation so every request's intermediate states
//!   and decisions are traceable in the logs.
//! - [`service`]: the Axum HTTP boundary translating between JSON/base64
//!   and the kernel.

pub mod error;
pub mod format;
pub mod hash;
pub mod iblt;
pub mod limits;
pub mod runlog;
pub mod service;
