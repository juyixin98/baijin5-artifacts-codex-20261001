//! Demo binary: runs the runtime with the real filesystem adapter and serves
//! the diagnostics/demo HTTP interface.
//!
//! Usage: `io-queue-runtime [path/to/config.toml]`

use io_queue_runtime::adapter::fs::FsAdapter;
use io_queue_runtime::config::RuntimeConfig;
use io_queue_runtime::diag::{self, DiagState};
use io_queue_runtime::journal::Journal;
use io_queue_runtime::runtime::{CoreEvent, RuntimeCore};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config_path = std::env::args().nth(1).map(PathBuf::from);
    let config = RuntimeConfig::load(config_path.as_deref())?;
    println!("config: {config:#?}");

    let core = RuntimeCore::new(
        config.capacity,
        config.buffer_count,
        config.default_timeout_ms,
        FsAdapter::new(),
    );
    let state = DiagState::new(core, config.journal_path.clone());

    // Driver task: feeds the core real time and persists core events.
    {
        let state = Arc::clone(&state);
        let journal_path = config.journal_path.clone();
        let sample_n = config.journal_sample_n;
        tokio::spawn(async move {
            let mut journal = Journal::open(&journal_path, sample_n)
                .expect("journal opens; check journal_path in config");
            let start = Instant::now();
            loop {
                let now_ms = start.elapsed().as_millis() as u64;
                let events = state.core.lock().expect("core mutex").poll(now_ms);
                for event in events {
                    match event {
                        CoreEvent::Finalized(record) => {
                            journal
                                .append_finalized(&record)
                                .expect("journal writable");
                        }
                        CoreEvent::Anomaly(anomaly) => {
                            journal
                                .append_anomaly(&anomaly)
                                .expect("journal writable");
                        }
                    }
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        });
    }

    let app = diag::router(state);
    let listener = tokio::net::TcpListener::bind(&config.diag_addr).await?;
    println!("diagnostics listening on http://{}", config.diag_addr);
    println!("try:");
    println!("  curl -s localhost:7878/diag/state");
    println!("  curl -s -XPOST localhost:7878/io/submit -H 'content-type: application/json' \\");
    println!("    -d '{{\"user_data\": 1234, \"op\": {{\"ReadFile\": {{\"path\": \"Cargo.toml\"}}}}}}'");
    println!("  curl -s localhost:7878/diag/records/1");
    axum::serve(listener, app).await?;
    Ok(())
}
