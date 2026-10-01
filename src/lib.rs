//! String collation-aware grouping/dedup comparison contract.
//!
//! Crate layout (each module has a real responsibility, see README):
//! - [`collation`]: versioned sort-key / equivalence-class rules.
//! - [`batch`]: Arrow2 typed columnar input (record identity + value).
//! - [`operators`]: sort grouping, hash grouping, dedup executors.
//! - [`state`]: resources, version-pinned sessions, diagnostic log, redaction.
//! - [`config`]: local settings.
//! - [`validate`]: the shared comparison-contract entry point and verdicts.
//! - [`api`]: thin Axum transport over [`validate`].

pub mod api;
pub mod batch;
pub mod collation;
pub mod config;
pub mod error;
pub mod operators;
pub mod state;
pub mod validate;

pub use config::Settings;
pub use state::AppState;
pub use validate::{verify, ApiRow, Category, Report, VerifyRequest};

/// Construct shared state from local configuration.
pub fn build_state() -> error::Result<std::sync::Arc<AppState>> {
    let settings = Settings::from_env()?;
    Ok(std::sync::Arc::new(AppState::new(settings)))
}
