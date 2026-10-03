//! Controllable adapter fixture.
//!
//! Nothing happens unless the test explicitly queues an event with
//! [`ScriptedAdapter::push_event`], so tests can interleave completions,
//! cancels, timeouts (by simply never pushing an event) and stale events
//! from reused handles with full determinism. All interactions are recorded
//! in `started` / `cancels` / `aborts` for later assertion.

use super::{AdapterEvent, CancelAck, IoAdapter};
use crate::model::{Generation, OpKind};
use std::collections::VecDeque;

#[derive(Default)]
pub struct ScriptedAdapter {
    events: VecDeque<AdapterEvent>,
    cancel_ack: Option<CancelAck>,
    /// Every `start` call, in order.
    pub started: Vec<(u16, Generation, OpKind)>,
    /// Every `cancel` call, in order.
    pub cancels: Vec<(u16, Generation)>,
    /// Every `abort` call, in order.
    pub aborts: Vec<(u16, Generation)>,
}

impl ScriptedAdapter {
    pub fn new() -> Self {
        Self::default()
    }

    /// Queue an event the core will see on its next `poll`.
    pub fn push_event(&mut self, event: AdapterEvent) {
        self.events.push_back(event);
    }

    /// Choose what future `cancel` calls acknowledge with.
    pub fn set_cancel_ack(&mut self, ack: CancelAck) {
        self.cancel_ack = Some(ack);
    }
}

impl IoAdapter for ScriptedAdapter {
    fn start(&mut self, slot: u16, generation: Generation, op: &OpKind) {
        self.started.push((slot, generation, op.clone()));
    }

    fn cancel(&mut self, slot: u16, generation: Generation) -> CancelAck {
        self.cancels.push((slot, generation));
        self.cancel_ack.unwrap_or(CancelAck::WillCancel)
    }

    fn abort(&mut self, slot: u16, generation: Generation) {
        self.aborts.push((slot, generation));
    }

    fn poll(&mut self) -> Vec<AdapterEvent> {
        self.events.drain(..).collect()
    }
}
