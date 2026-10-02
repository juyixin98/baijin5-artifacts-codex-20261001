//! Tracing initialisation and run identity.
//!
//! Every server run and every integration test gets a `run_id` so log lines
//! can be correlated back to the input/run that produced them.

use tracing_subscriber::EnvFilter;

/// Initialise the global tracing subscriber (idempotent).
pub fn init_tracing() {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("mmap_model=info,tower_http=warn"));
    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(true)
        .try_init();
}

/// A process-unique run identifier: timestamp + pid, no external deps.
pub fn run_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("run-{nanos:x}-{}", std::process::id())
}

/// Crate version, for log correlation.
pub fn crate_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
