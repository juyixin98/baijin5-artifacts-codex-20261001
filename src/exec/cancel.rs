//! Cooperative cancellation.
//!
//! Operators poll a [`Token`] at row-batch and spill boundaries.  A token is a
//! cheap `Arc<AtomicBool>` clone, so it can be handed to the spill loop and
//! registered in the server-side cancellation registry simultaneously.
//!
//! For deterministic testing (and diagnostic demos) a token can be created with
//! [`Token::with_auto_merge_cancel`]: after the given number of *merge* records
//! have passed a cancellation point, the token flips itself.  Ingestion uses
//! plain [`Token::check`], so the automatic trigger can only fire during the
//! merge phase, exactly where cancellation/resume is exercised.

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::Arc;

use crate::error::{Error, ErrorKind, Result};

/// Cloneable cancellation flag.
#[derive(Clone)]
pub struct Token {
    inner: Arc<Inner>,
}

struct Inner {
    cancelled: AtomicBool,
    /// Merge-record ticks remaining before auto-cancel. `-1` disables it.
    merge_ticks_left: AtomicI64,
}

impl Default for Token {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Debug for Token {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Token")
            .field("cancelled", &self.is_cancelled())
            .finish_non_exhaustive()
    }
}

impl Token {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Inner {
                cancelled: AtomicBool::new(false),
                merge_ticks_left: AtomicI64::new(-1),
            }),
        }
    }

    /// Auto-cancel after `after` merge-record cancellation points.
    pub fn with_auto_merge_cancel(after: u64) -> Self {
        Self {
            inner: Arc::new(Inner {
                cancelled: AtomicBool::new(false),
                merge_ticks_left: AtomicI64::new(after.max(1) as i64),
            }),
        }
    }

    pub fn cancel(&self) {
        self.inner.cancelled.store(true, Ordering::SeqCst);
    }

    /// Returns `true` if cancellation has been requested.
    pub fn is_cancelled(&self) -> bool {
        self.inner.cancelled.load(Ordering::SeqCst)
    }

    /// Raise [`ErrorKind::Cancelled`] at a cancellation point.
    pub fn check(&self) -> Result<()> {
        if self.is_cancelled() {
            Err(cancelled_error())
        } else {
            Ok(())
        }
    }

    /// Cancellation point used by the merge loop.  Decrements the optional
    /// auto-cancel budget before the usual flag check.
    pub fn tick_merge(&self) -> Result<()> {
        let remaining = self.inner.merge_ticks_left.load(Ordering::Relaxed);
        if remaining >= 0 {
            let next = remaining - 1;
            self.inner.merge_ticks_left.store(next, Ordering::Relaxed);
            if next <= 0 {
                self.inner.cancelled.store(true, Ordering::SeqCst);
            }
        }
        self.check()
    }
}

fn cancelled_error() -> Error {
    Error::new(
        ErrorKind::Cancelled,
        "cancellation observed at operator boundary; spill state is durable",
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_is_shared_across_clones() {
        let t1 = Token::new();
        let t2 = t1.clone();
        assert!(t1.check().is_ok());
        t2.cancel();
        assert_eq!(t1.check().unwrap_err().kind, ErrorKind::Cancelled);
    }

    #[test]
    fn auto_cancel_fires_only_after_merge_ticks() {
        let t = Token::with_auto_merge_cancel(2);
        // Plain checks (ingestion) never consume the merge budget.
        t.check().unwrap();
        t.check().unwrap();
        t.tick_merge().unwrap();
        assert_eq!(t.tick_merge().unwrap_err().kind, ErrorKind::Cancelled);
        // A clone observes the flipped flag.
        assert!(t.clone().is_cancelled());
    }
}
