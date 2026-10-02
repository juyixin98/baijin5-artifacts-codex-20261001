//! Cooperative cancellation shared across an operator tree.
//!
//! One [`CancelToken`] is created per query and cloned into every operator of
//! the plan. Cancelling it therefore propagates along the whole tree: each
//! operator observes the cancellation at its next pull boundary (or while it
//! is blocked inside a cancellable sleep) and aborts with
//! `QueryError::Cancelled`.
//!
//! The *first* cancellation wins, so a deadline firing after a user cancel
//! never rewrites the reason.

use tokio::sync::watch;

use crate::error::{CancelKind, QueryError};

#[derive(Debug, Clone)]
pub struct CancelToken {
    tx: watch::Sender<Option<CancelKind>>,
}

impl Default for CancelToken {
    fn default() -> Self {
        Self::new()
    }
}

impl CancelToken {
    pub fn new() -> Self {
        let (tx, _) = watch::channel(None);
        Self { tx }
    }

    /// Cancel with `kind`. Returns `true` if this call performed the
    /// cancellation (i.e. the token was not already cancelled).
    pub fn cancel(&self, kind: CancelKind) -> bool {
        self.tx.send_if_modified(|slot| {
            if slot.is_some() {
                return false;
            }
            *slot = Some(kind);
            true
        })
    }

    pub fn kind(&self) -> Option<CancelKind> {
        *self.tx.borrow()
    }

    pub fn is_cancelled(&self) -> bool {
        self.kind().is_some()
    }

    /// Non-async checkpoint used by operators between units of work.
    pub fn check(&self) -> Result<(), QueryError> {
        match self.kind() {
            Some(kind) => Err(QueryError::cancelled(kind)),
            None => Ok(()),
        }
    }

    /// Resolves once the token is cancelled. Race-free: a cancellation that
    /// happened before this call is observed immediately.
    pub async fn cancelled(&self) -> CancelKind {
        let mut rx = self.tx.subscribe();
        // wait_for returns as soon as the closure matches, including for the
        // value already in the channel.
        let kind = match rx.wait_for(|slot| slot.is_some()).await {
            Ok(reference) => *reference,
            Err(_) => Some(CancelKind::User), // sender dropped: treat as cancelled
        };
        kind.expect("wait_for predicate guarantees Some")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn first_cancel_reason_wins() {
        let token = CancelToken::new();
        assert!(token.cancel(CancelKind::User));
        assert!(!token.cancel(CancelKind::Timeout));
        assert_eq!(token.kind(), Some(CancelKind::User));
        let err = token.check().unwrap_err();
        assert_eq!(err.category(), crate::error::ErrorCategory::CancelledUser);
    }

    #[tokio::test]
    async fn cancelled_future_observes_prior_cancel() {
        let token = CancelToken::new();
        token.cancel(CancelKind::Timeout);
        assert_eq!(token.cancelled().await, CancelKind::Timeout);
    }
}
