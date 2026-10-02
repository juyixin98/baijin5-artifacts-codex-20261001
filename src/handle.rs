//! Connection table with generation counters (连接世代).
//!
//! A connection slot is reused after it is fully closed, but every open
//! bumps the generation. Handles carrying an old generation are rejected,
//! which is what makes late completions from a previous incarnation of the
//! slot harmless.

use serde::Serialize;

use crate::model::{ConnHandle, ConnId, Generation};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConnLifecycle {
    Open,
    /// Close requested; resources are released only after every associated
    /// completion is final and every leased buffer is back.
    Draining,
    Closed,
}

#[derive(Debug, Clone, Copy)]
struct ConnSlot {
    generation: u64,
    lifecycle: ConnLifecycle,
    pending_records: u32,
    outstanding_buffers: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandleError {
    UnknownConnection,
    StaleGeneration { expected: u64, found: u64 },
    NotOpen { lifecycle: ConnLifecycle },
    NotOwner,
}

impl std::fmt::Display for HandleError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HandleError::UnknownConnection => write!(f, "unknown connection"),
            HandleError::StaleGeneration { expected, found } => write!(
                f,
                "stale generation: handle carries {found}, slot is at {expected} (slot was reused)"
            ),
            HandleError::NotOpen { lifecycle } => {
                write!(f, "connection is not open (lifecycle: {lifecycle:?})")
            }
            HandleError::NotOwner => {
                write!(f, "submission does not belong to this connection handle")
            }
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct ConnView {
    pub conn: ConnId,
    pub generation: Generation,
    pub lifecycle: ConnLifecycle,
    pub pending_records: u32,
    pub outstanding_buffers: u32,
}

#[derive(Debug, Default)]
pub struct ConnectionTable {
    slots: Vec<ConnSlot>,
    free: Vec<usize>,
}

impl ConnectionTable {
    pub fn new() -> Self {
        Self::default()
    }

    /// Open a connection, reusing a fully closed slot when possible.
    /// Reuse always bumps the generation.
    pub fn open(&mut self) -> ConnHandle {
        if let Some(idx) = self.free.pop() {
            let slot = &mut self.slots[idx];
            slot.generation += 1;
            slot.lifecycle = ConnLifecycle::Open;
            slot.pending_records = 0;
            slot.outstanding_buffers = 0;
            ConnHandle {
                conn: ConnId(idx as u64),
                generation: Generation(slot.generation),
            }
        } else {
            self.slots.push(ConnSlot {
                generation: 1,
                lifecycle: ConnLifecycle::Open,
                pending_records: 0,
                outstanding_buffers: 0,
            });
            ConnHandle {
                conn: ConnId((self.slots.len() - 1) as u64),
                generation: Generation(1),
            }
        }
    }

    fn slot(&self, conn: ConnId) -> Option<&ConnSlot> {
        self.slots.get(conn.0 as usize)
    }

    /// Validate generation and that the connection is not closed.
    pub fn validate(&self, handle: ConnHandle) -> Result<(), HandleError> {
        let slot = self.slot(handle.conn).ok_or(HandleError::UnknownConnection)?;
        if slot.generation != handle.generation.0 {
            return Err(HandleError::StaleGeneration {
                expected: slot.generation,
                found: handle.generation.0,
            });
        }
        if slot.lifecycle == ConnLifecycle::Closed {
            return Err(HandleError::NotOpen {
                lifecycle: ConnLifecycle::Closed,
            });
        }
        Ok(())
    }

    /// Validate as above, additionally requiring `Open` (for new submissions).
    pub fn validate_open(&self, handle: ConnHandle) -> Result<(), HandleError> {
        self.validate(handle)?;
        let slot = self.slot(handle.conn).expect("validated slot exists");
        if slot.lifecycle != ConnLifecycle::Open {
            return Err(HandleError::NotOpen {
                lifecycle: slot.lifecycle,
            });
        }
        Ok(())
    }

    /// Begin closing: Open -> Draining. If nothing is outstanding the slot is
    /// freed immediately. Returns the resulting view.
    pub fn begin_close(&mut self, conn: ConnId) -> ConnView {
        let idx = conn.0 as usize;
        let slot = &mut self.slots[idx];
        if slot.lifecycle == ConnLifecycle::Open {
            slot.lifecycle = ConnLifecycle::Draining;
        }
        self.maybe_free(conn);
        self.view(conn).expect("slot exists")
    }

    /// Free the slot once Draining with nothing pending. Returns true if the
    /// slot transitioned to Closed.
    pub fn maybe_free(&mut self, conn: ConnId) -> bool {
        let idx = conn.0 as usize;
        let Some(slot) = self.slots.get(idx) else {
            return false;
        };
        if slot.lifecycle == ConnLifecycle::Draining
            && slot.pending_records == 0
            && slot.outstanding_buffers == 0
        {
            self.slots[idx].lifecycle = ConnLifecycle::Closed;
            self.free.push(idx);
            return true;
        }
        false
    }

    pub fn on_submit(&mut self, conn: ConnId) {
        if let Some(slot) = self.slots.get_mut(conn.0 as usize) {
            slot.pending_records += 1;
        }
    }

    pub fn on_finalize(&mut self, conn: ConnId) {
        if let Some(slot) = self.slots.get_mut(conn.0 as usize) {
            slot.pending_records = slot.pending_records.saturating_sub(1);
        }
    }

    pub fn on_buffer_lease(&mut self, conn: ConnId) {
        if let Some(slot) = self.slots.get_mut(conn.0 as usize) {
            slot.outstanding_buffers += 1;
        }
    }

    pub fn on_buffer_release(&mut self, conn: ConnId) {
        if let Some(slot) = self.slots.get_mut(conn.0 as usize) {
            slot.outstanding_buffers = slot.outstanding_buffers.saturating_sub(1);
        }
    }

    pub fn view(&self, conn: ConnId) -> Option<ConnView> {
        self.slot(conn).map(|s| ConnView {
            conn,
            generation: Generation(s.generation),
            lifecycle: s.lifecycle,
            pending_records: s.pending_records,
            outstanding_buffers: s.outstanding_buffers,
        })
    }

    pub fn open_count(&self) -> usize {
        self.slots
            .iter()
            .filter(|s| s.lifecycle != ConnLifecycle::Closed)
            .count()
    }
}
