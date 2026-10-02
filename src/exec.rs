//! Per-query execution context: run id, cancellation, deadline, resources.
//!
//! The context is shared (`Arc`) by every operator of one plan. It owns the
//! deadline watcher task. Teardown is two-tiered:
//! - [`ExecutionContext::shutdown`] (async, preferred): signals the watcher
//!   to exit and awaits it — afterwards `tasks_live` is guaranteed to be 0.
//! - [`ExecutionContext::close`] (sync, also runs on `Drop`): aborts the
//!   watcher; correct but the task's death is not awaited.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::cancel::CancelToken;
use crate::error::CancelKind;
use crate::resources::ResourceRegistry;

static RUN_SEQ: AtomicU64 = AtomicU64::new(0);

/// Unique, log-friendly run identifier: `run-<unix_millis>-<seq>`.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize)]
pub struct RunId(pub String);

impl RunId {
    pub fn new() -> Self {
        let millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0);
        let seq = RUN_SEQ.fetch_add(1, Ordering::AcqRel);
        Self(format!("run-{millis}-{seq}"))
    }
}

impl Default for RunId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for RunId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

pub struct ExecutionContext {
    pub run_id: RunId,
    pub cancel: CancelToken,
    pub resources: Arc<ResourceRegistry>,
    pub started_at: Instant,
    shutdown_tx: tokio::sync::watch::Sender<bool>,
    watcher: Mutex<Option<tokio::task::JoinHandle<()>>>,
    closed: AtomicBool,
}

impl ExecutionContext {
    /// Create a context with a fresh run id. See [`Self::with_run_id`].
    pub fn new(resources: Arc<ResourceRegistry>, timeout: Option<Duration>) -> Arc<Self> {
        Self::with_run_id(RunId::new(), resources, timeout)
    }

    /// Create a context with a caller-chosen run id (so e.g. the spill
    /// directory can be named after it). If `timeout` is set, a tracked
    /// watcher task cancels the token with [`CancelKind::Timeout`] when the
    /// deadline elapses — this is the only place timeout cancellation
    /// originates, so it can never be confused with a user cancel.
    pub fn with_run_id(
        run_id: RunId,
        resources: Arc<ResourceRegistry>,
        timeout: Option<Duration>,
    ) -> Arc<Self> {
        let cancel = CancelToken::new();
        let (shutdown_tx, _) = tokio::sync::watch::channel(false);
        let watcher = timeout.map(|duration| {
            let token = cancel.clone();
            let mut shutdown_rx = shutdown_tx.subscribe();
            resources.spawn_tracked(async move {
                tokio::select! {
                    _ = tokio::time::sleep(duration) => {
                        token.cancel(CancelKind::Timeout);
                    }
                    _ = shutdown_rx.changed() => {} // context closed: exit quietly
                }
            })
        });
        Arc::new(Self {
            run_id,
            cancel,
            resources,
            started_at: Instant::now(),
            shutdown_tx,
            watcher: Mutex::new(watcher),
            closed: AtomicBool::new(false),
        })
    }

    /// Idempotent sync teardown: signals the watcher to exit and aborts it
    /// as a backstop. Does not wait for the task to die; use
    /// [`Self::shutdown`] when reclamation must be observable.
    pub fn close(&self) {
        if self.closed.swap(true, Ordering::AcqRel) {
            return;
        }
        let _ = self.shutdown_tx.send(true);
        if let Some(handle) = self.watcher.lock().expect("watcher mutex").take() {
            handle.abort();
        }
    }

    /// Idempotent async teardown: signals the watcher to exit and awaits its
    /// termination, so `tasks_live` is guaranteed to reach zero before this
    /// returns. Tests and the service use this for deterministic assertions.
    pub async fn shutdown(&self) {
        if self.closed.swap(true, Ordering::AcqRel) {
            return;
        }
        let _ = self.shutdown_tx.send(true);
        let handle = self.watcher.lock().expect("watcher mutex").take();
        if let Some(handle) = handle {
            // The watcher exits normally on the shutdown signal; awaiting a
            // normally-completed task guarantees its guards were dropped.
            let _ = handle.await;
        }
    }
}

impl Drop for ExecutionContext {
    fn drop(&mut self) {
        self.close();
    }
}
