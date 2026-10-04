//! Teaching-grade two-party Diffie-Hellman private set intersection (PSI)
//! over the ristretto255 prime-order group.
//!
//! Module map:
//! - [`crypto`]: hash-to-group, point encode/decode with mandatory validation.
//! - [`protocol`]: party state machines (A = intersection receiver, B = responder).
//! - [`store`]: SQLite-backed session/message/audit persistence.
//! - [`audit`]: redacted audit records (counts + payload digests only).
//! - [`server`]: Axum HTTP transport; never sees raw elements.
//! - [`client`]: client-side driver used by the `psi-client` binary.
//! - [`verify`]: independent reference implementations used by tests and demo.
//! - [`error`]: shared error taxonomy with stable machine-readable codes.
//! - [`config`]: server configuration (env-driven).

pub mod audit;
pub mod client;
pub mod config;
pub mod crypto;
pub mod error;
pub mod protocol;
pub mod server;
pub mod store;
pub mod verify;
