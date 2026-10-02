//! The pull-based operator contract and its lifecycle state machine.
//!
//! ## Pull model
//! Every operator implements [`SourceOperator::pull`] (or wraps an upstream via
//! [`UnaryOperator`] / [`BinaryOperator`]). The runtime (or the Axum handler)
//! repeatedly calls [`Operator::next`], getting `Some(Batch)` until `None`.
//!
//! ## Lifecycle guarantees enforced here, not by each operator
//! A single embedded [`OperatorCore`] gives every operator the same hard
//! guarantees, so individual operators cannot forget them:
//!
//! * **Poisoned stream after error.** Once `pull` returns an error the operator
//!   enters [`OpState::Failed`]. Every later `next` returns the *same* terminal
//!   error and never invokes `pull` (and therefore never touches a possibly
//!   corrupted upstream) again. This satisfies "do not keep consuming a damaged
//!   downstream stream after an error".
//! * **Exhaustion is terminal.** After `pull` returns `None`, later `next` calls
//!   keep returning `None` without re-entering the operator.
//! * **Idempotent, single-release close.** [`Operator::shutdown`] may be called
//!   any number of times, including after an error or from a cancellation
//!   unwind path; the operator's [`Operator::release`] runs at most once, so
//!   spill files and buffers are never double-freed.
//! * **Cancel vs timeout.** Control signals are checked at every `next` entry
//!   and at intra-operator safe points; cancellation and timeout stay distinct
//!   (see [`crate::cancel::Control::check`]).
//!
//! Returned data is unaffected by any of this: a [`Batch`] is fully owned and
//! borrows nothing from the operator or the [`Control`], so batches already
//! handed to a consumer remain readable after failure/cancel/close.

use std::sync::Arc;

use crate::batch::{Batch, Schema};
use crate::cancel::Control;
use crate::error::{ErrorKind, QueryError, QueryResult};

/// Lifecycle state of one operator.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpState {
    /// May produce more batches.
    Running,
    /// Upstream returned end-of-stream; `next` yields `None` forever.
    Exhausted,
    /// Saw an error; `next` yields that error forever; close still required.
    Failed,
    /// Resources released. Terminal state.
    Closed,
}

impl OpState {
    pub fn as_str(self) -> &'static str {
        match self {
            OpState::Running => "running",
            OpState::Exhausted => "exhausted",
            OpState::Failed => "failed",
            OpState::Closed => "closed",
        }
    }
}

/// Shared lifecycle bookkeeping. Every concrete operator owns one.
pub struct OperatorCore {
    name: String,
    state: OpState,
    /// Set once in `Failed`; cloned out on every subsequent poisoned `next`.
    terminal: Option<QueryError>,
    pulls: u64,
    released: bool,
}

impl OperatorCore {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            state: OpState::Running,
            terminal: None,
            pulls: 0,
            released: false,
        }
    }

    pub fn name(&self) -> &str {
        &self.name
    }
    pub fn state(&self) -> OpState {
        self.state
    }
    pub fn pulls(&self) -> u64 {
        self.pulls
    }
    pub fn terminal_error(&self) -> Option<&QueryError> {
        self.terminal.as_ref()
    }
    pub fn was_released(&self) -> bool {
        self.released
    }
}

/// Result of the template method deciding whether the concrete `pull` may run.
enum Gate {
    Proceed,
    Exhausted,
    Failed(QueryError),
    Closed,
}

/// Common operator interface. The only template methods are [`next`](Self::next)
/// and [`shutdown`](Self::shutdown); concrete operators supply data production
/// via `pull` and resource release via `release`.
pub trait Operator {
    fn name(&self) -> &str;
    fn schema_out(&self) -> Arc<Schema>;
    fn core(&self) -> &OperatorCore;
    fn core_mut(&mut self) -> &mut OperatorCore;

    /// Produce the next batch. Called only while `Running` and only when the
    /// control plane is clear; the wrapper handles all other states.
    fn pull(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>>;

    /// Release owned resources (spill files, buffers). Guaranteed by the
    /// wrapper to be invoked **at most once**; implementations should still
    /// write release code that is safe if the operator never produced anything.
    /// The control reference (when available) is forwarded so composite
    /// operators can close children with diagnostics intact.
    fn release(&mut self, _ctrl: Option<&Control>) {}

    /// Pull the next batch with full lifecycle enforcement.
    fn next(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>> {
        let name = self.name().to_string();
        let gate = match self.core().state {
            OpState::Running => Gate::Proceed,
            OpState::Exhausted => Gate::Exhausted,
            OpState::Failed => Gate::Failed(
                self.core()
                    .terminal
                    .clone()
                    .expect("failed operator carries an error"),
            ),
            OpState::Closed => Gate::Closed,
        };

        match gate {
            Gate::Exhausted => Ok(None),
            Gate::Failed(e) => {
                ctrl.diag()
                    .warn(&name, "failed", format!("next() suppressed: {e}"));
                Err(e)
            }
            Gate::Closed => {
                Err(QueryError::state_conflict("next() called after shutdown()").at(name))
            }
            Gate::Proceed => {
                // Cooperative cancellation/deadline check before entering the
                // concrete operator. Distinguishes cancel from timeout.
                if let Err(e) = ctrl.check() {
                    return self.fail(ctrl, e);
                }
                self.core_mut().pulls += 1;
                match self.pull(ctrl) {
                    Ok(Some(batch)) => Ok(Some(batch)),
                    Ok(None) => {
                        self.core_mut().state = OpState::Exhausted;
                        ctrl.diag().set_state(&name, OpState::Exhausted.as_str());
                        ctrl.diag().info(&name, "exhausted", "end of stream");
                        Ok(None)
                    }
                    Err(e) => self.fail(ctrl, e),
                }
            }
        }
    }

    /// Record a terminal error, poison the stream and report it.
    fn fail(&mut self, ctrl: &Control, e: QueryError) -> QueryResult<Option<Batch>> {
        let name = self.name().to_string();
        let e = if e.context().operator.is_none() {
            e.at(name.clone())
        } else {
            e
        };
        let core = self.core_mut();
        core.state = OpState::Failed;
        core.terminal = Some(e.clone());
        ctrl.diag().set_state(&name, "failed");
        ctrl.diag().error(
            &name,
            "failed",
            format!("kind={} {}", e.kind(), e.message()),
        );
        Err(e)
    }

    /// Close the operator. Safe to call repeatedly and from multiple unwind
    /// paths; `release` runs at most once. Always succeeds — a release problem
    /// is logged but does not panic, because close happens during teardown.
    fn shutdown(&mut self, ctrl: Option<&Control>) {
        let name = self.name().to_string();
        if self.core().state == OpState::Closed {
            if let Some(c) = ctrl {
                c.diag().info(&name, "closed", "shutdown() repeated; no-op");
            }
            return;
        }
        if !self.core().released {
            self.core_mut().released = true;
            // Set the flag *before* running release so a re-entrant shutdown
            // (e.g. from a Drop path) cannot release twice even on panic.
            self.release(ctrl);
        }
        self.core_mut().state = OpState::Closed;
        if let Some(c) = ctrl {
            c.diag().set_state(&name, OpState::Closed.as_str());
            c.diag()
                .info(&name, "closed", "resources released exactly once");
        }
    }
}

/// Convenience: drain an operator to completion (or first error), returning all
/// pulled batches. The caller is responsible for `shutdown` afterwards (use
/// [`run_to_completion`] for the full guarded lifecycle).
pub fn drain<O: Operator>(op: &mut O, ctrl: &Control) -> QueryResult<Vec<Batch>> {
    let mut out = Vec::new();
    loop {
        match op.next(ctrl) {
            Ok(Some(b)) => out.push(b),
            Ok(None) => return Ok(out),
            Err(e) => return Err(e),
        }
    }
}

/// Full guarded execution: drain, then always exactly-once shutdown, mapping
/// the outcome to a terminal diagnostic. Already-returned batches are given
/// back to the caller even on error (they remain readable).
pub fn run_to_completion<O: Operator>(
    op: &mut O,
    ctrl: &Control,
) -> (Vec<Batch>, Option<QueryError>) {
    let mut batches = Vec::new();
    let mut err = None;
    loop {
        match op.next(ctrl) {
            Ok(Some(b)) => batches.push(b),
            Ok(None) => break,
            Err(e) => {
                err = Some(e);
                break;
            }
        }
    }
    op.shutdown(Some(ctrl));
    match &err {
        None => ctrl
            .diag()
            .terminate(crate::diag::TerminalKind::Completed, "stream exhausted"),
        Some(e) => ctrl.diag().terminate(
            crate::diag::TerminalKind::Failed(e.kind()),
            format!("{}", e.kind()),
        ),
    }
    (batches, err)
}

/// True when an error is a control (cancel/timeout) signal rather than a fault.
pub fn is_control(e: &QueryError) -> bool {
    matches!(e.kind(), ErrorKind::Timeout | ErrorKind::Cancelled)
}
