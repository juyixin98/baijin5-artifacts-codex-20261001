//! Adapter boundary between the runtime core and the actual IO backend.
//!
//! The core is synchronous and deterministic; adapters encapsulate all
//! asynchrony. Two implementations ship with the crate:
//!
//! - [`scripted::ScriptedAdapter`] — fully controllable fixture used by the
//!   test-suite to interleave success, cancel, timeout and handle reuse.
//! - [`fs::FsAdapter`] — real local-filesystem backend used by the demo
//!   binary.

use crate::model::{Generation, OpKind};

pub mod fs;
pub mod scripted;

/// Acknowledgement returned synchronously by [`IoAdapter::cancel`].
///
/// This is only an acknowledgement of the *request*. Even `WillCancel` does
/// not guarantee the record finalizes as `Cancelled`: the operation may have
/// completed concurrently, and whichever terminal event arrives first wins.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CancelAck {
    /// The adapter will try to cancel and will emit
    /// [`AdapterEvent::Cancelled`] if it succeeds.
    WillCancel,
    /// The backend cannot cancel in-flight operations; the completion will
    /// arrive normally. Demonstrates "cancel accepted" != "IO did not run".
    Unsupported,
    /// The adapter does not know this slot (already finished, never started).
    UnknownSlot,
}

/// Events an adapter pushes back into the core. Every event carries the
/// `(slot, generation)` pair it believes it belongs to; the core re-validates
/// both before attributing the event to a record.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AdapterEvent {
    /// The operation finished. `Ok(bytes)` on success, `Err(message)` on
    /// backend failure.
    Completed {
        slot: u16,
        generation: Generation,
        result: Result<usize, String>,
    },
    /// A previously accepted cancel request took effect before the operation
    /// ran.
    Cancelled { slot: u16, generation: Generation },
}

/// Backend contract. Implementations must be `Send` so the core can live
/// behind a mutex shared with the diagnostics server.
pub trait IoAdapter: Send + 'static {
    /// Begin executing `op` on behalf of `(slot, generation)`.
    fn start(&mut self, slot: u16, generation: Generation, op: &OpKind);

    /// Best-effort cancellation of an in-flight operation.
    fn cancel(&mut self, slot: u16, generation: Generation) -> CancelAck;

    /// Best-effort cleanup after the core declared a timeout. The record is
    /// already finalized; this only releases backend-side resources. Any
    /// event the backend still emits afterwards will be rejected by the core
    /// as stale.
    fn abort(&mut self, slot: u16, generation: Generation);

    /// Drain all events that have arrived since the last poll.
    fn poll(&mut self) -> Vec<AdapterEvent>;
}
