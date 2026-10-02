//! Device adapter abstraction. The engine talks to the "device" only
//! through this trait, which lets tests plug in a fully controllable
//! scripted adapter and interleave success, cancel, timeout and faults.

use crate::model::{FailureKind, IoOp, UserData};

pub mod scripted;

/// Acknowledgement of a cancel request by the device.
///
/// Crucially this is *not* an outcome: `WillCancel` only means the device
/// accepted the request. The authoritative outcome always arrives as a
/// completion (or a timeout).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CancelAck {
    WillCancel,
    NotFound,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IoResult {
    Success { bytes: u64 },
    Failed { kind: FailureKind },
    /// The device honoured the cancel; `io_performed` reports whether the
    /// IO physically happened anyway (partial execution).
    Cancelled { io_performed: bool },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AdapterCompletion {
    pub token: UserData,
    pub result: IoResult,
}

pub trait IoAdapter: Send {
    fn start(&mut self, token: UserData, op: &IoOp, now_ms: u64);
    fn cancel(&mut self, token: UserData) -> CancelAck;
    /// Drain completions that are ready at `now_ms`.
    fn poll(&mut self, now_ms: u64) -> Vec<AdapterCompletion>;
    fn in_flight(&self) -> usize;
}
