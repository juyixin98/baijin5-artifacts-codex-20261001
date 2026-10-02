//! Cancellation tokens and per-pull control plane.
//!
//! A [`CancellationToken`] is a shared, owner-held switch. The owner (an HTTP
//! handler, a test) flips it; every operator observes the same flag through the
//! [`Control`] reference it receives on every pull. This is deliberately *not*
//! a deadline: cancellation is cooperative and explicit, whereas a timeout is a
//! wall-clock property of the execution. Operators check [`Control::check`] at
//! every safe point — between batches, inside a blocking build, and while a
//! slow scan sleeps — so a cancel wins promptly even when an operator is blocked
//! deep in a sort or join build.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::diag::RunDiag;
use crate::error::{QueryError, QueryResult};

/// A cloneable cancellation switch. All clones share one atomic bit.
#[derive(Clone)]
pub struct CancellationToken {
    cancelled: Arc<AtomicBool>,
}

impl Default for CancellationToken {
    fn default() -> Self {
        Self::new()
    }
}

impl CancellationToken {
    pub fn new() -> Self {
        Self {
            cancelled: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Flip the switch. Idempotent: cancelling an already-cancelled token is a
    /// no-op and returns `false`.
    pub fn cancel(&self) -> bool {
        !self.cancelled.swap(true, Ordering::SeqCst)
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::SeqCst)
    }
}

/// Per-pull control reference handed to every operator.
///
/// Holds the cancellation token, an optional wall-clock deadline, and the
/// diagnostic sink. Cheap to pass by reference; it owns no query data, so a
/// [`crate::batch::Batch`] already returned to a consumer stays valid for as
/// long as the consumer holds it regardless of what happens here.
pub struct Control {
    token: CancellationToken,
    deadline: Option<Instant>,
    diag: Arc<RunDiag>,
}

impl Control {
    pub fn new(token: CancellationToken, deadline: Option<Instant>, diag: Arc<RunDiag>) -> Self {
        Self {
            token,
            deadline,
            diag,
        }
    }

    /// A control with no deadline (tests / library use).
    pub fn unbounded(diag: Arc<RunDiag>) -> Self {
        Self::new(CancellationToken::new(), None, diag)
    }

    pub fn token(&self) -> &CancellationToken {
        &self.token
    }

    pub fn diag(&self) -> &RunDiag {
        &self.diag
    }

    /// Returns how much wall-clock budget remains, if a deadline is set.
    pub fn remaining(&self) -> Option<Duration> {
        self.deadline
            .map(|d| d.saturating_duration_since(Instant::now()))
    }

    /// Cooperative safe-point.
    ///
    /// Cancellation is tested *before* the deadline: if a query was both
    /// cancelled and past its deadline, the explicit owner action is reported
    /// ([`ErrorKind::Cancelled`]) rather than the timer. This is what makes the
    /// two terminal causes distinguishable in a cancellation/timeout race.
    ///
    /// [`ErrorKind::Cancelled`]: crate::error::ErrorKind::Cancelled
    pub fn check(&self) -> QueryResult<()> {
        if self.token.is_cancelled() {
            return Err(QueryError::cancelled("query cancelled by owner"));
        }
        if let Some(deadline) = self.deadline {
            if Instant::now() >= deadline {
                return Err(QueryError::timeout("query deadline elapsed"));
            }
        }
        Ok(())
    }

    /// Sleep in small cancel-aware slices instead of one blocking call, so a
    /// slow source reacts to both cancel and deadline promptly. Used by the
    /// blocking scan fixture.
    pub fn interruptible_sleep(&self, total: Duration) -> QueryResult<()> {
        let step = Duration::from_millis(5);
        let mut waited = Duration::ZERO;
        while waited < total {
            self.check()?;
            let cur = step.min(total - waited);
            std::thread::sleep(cur);
            waited += cur;
        }
        self.check()
    }
}
