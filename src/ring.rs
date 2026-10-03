//! Slot table: the submission queue's fixed-capacity ring.
//!
//! The table owns one slot per unit of queue capacity. A slot is either
//! `Free` or holds the [`ActiveRecord`] of one in-flight submission. The
//! generation of a slot is bumped on every finalize, so a `Handle` minted
//! for a previous occupant can never validate against a new one — this is
//! the mechanism that keeps late completions from hitting reused handles.

use crate::model::{Generation, Handle, Millis, OpKind, RecordId};
use crate::resources::BufferId;
use serde::Serialize;

/// State of one in-flight submission.
#[derive(Clone, Debug)]
pub struct ActiveRecord {
    pub record_id: RecordId,
    pub user_data: u64,
    pub op: OpKind,
    pub deadline_ms: Millis,
    pub submitted_at_ms: Millis,
    pub cancel_requested: bool,
    pub buffer: Option<BufferId>,
}

#[derive(Clone, Debug)]
struct Slot {
    generation: u32,
    active: Option<ActiveRecord>,
}

/// Serializable view of one slot for diagnostics.
#[derive(Clone, Debug, Serialize)]
pub struct SlotView {
    pub slot: u16,
    pub generation: u32,
    pub state: &'static str,
    pub record_id: Option<u64>,
    pub cancel_requested: Option<bool>,
    pub deadline_ms: Option<Millis>,
}

#[derive(Debug)]
pub struct SlotTable {
    slots: Vec<Slot>,
    free: Vec<u16>,
}

impl SlotTable {
    pub fn new(capacity: usize) -> Self {
        Self {
            slots: (0..capacity)
                .map(|_| Slot {
                    generation: 0,
                    active: None,
                })
                .collect(),
            // Reversed so allocation prefers low slot numbers (deterministic
            // slot reuse order, which tests rely on).
            free: (0..capacity as u16).rev().collect(),
        }
    }

    pub fn capacity(&self) -> usize {
        self.slots.len()
    }

    pub fn in_flight(&self) -> usize {
        self.capacity() - self.free.len()
    }

    /// Allocate a slot for `record`. Returns `None` when the queue is full —
    /// the caller turns that into explicit backpressure.
    pub fn alloc(&mut self, record: ActiveRecord) -> Option<Handle> {
        let idx = self.free.pop()?;
        let slot = &mut self.slots[idx as usize];
        debug_assert!(slot.active.is_none());
        slot.active = Some(record);
        Some(Handle {
            slot: idx,
            generation: Generation(slot.generation),
        })
    }

    /// Look up the active record for `handle`, validating the generation.
    pub fn get_active(&self, handle: Handle) -> Option<&ActiveRecord> {
        let slot = self.slots.get(handle.slot as usize)?;
        if slot.generation != handle.generation.0 {
            return None;
        }
        slot.active.as_ref()
    }

    /// Mutable variant of [`SlotTable::get_active`].
    pub fn get_active_mut(&mut self, handle: Handle) -> Option<&mut ActiveRecord> {
        let slot = self.slots.get_mut(handle.slot as usize)?;
        if slot.generation != handle.generation.0 {
            return None;
        }
        slot.active.as_mut()
    }

    /// Current generation of a slot, if the slot index exists. Diagnostics
    /// and error reporting use this to explain *why* a handle was rejected.
    pub fn current_generation(&self, slot: u16) -> Option<u32> {
        self.slots.get(slot as usize).map(|s| s.generation)
    }

    /// Whether the slot currently holds an in-flight record (any generation).
    pub fn is_active(&self, slot: u16) -> bool {
        self.slots
            .get(slot as usize)
            .map(|s| s.active.is_some())
            .unwrap_or(false)
    }

    /// Finalize the record in `handle`'s slot: remove it, bump the
    /// generation and return the slot to the free pool. Returns the removed
    /// record, or `None` if the handle did not validate.
    pub fn finalize(&mut self, handle: Handle) -> Option<ActiveRecord> {
        {
            let slot = self.slots.get(handle.slot as usize)?;
            if slot.generation != handle.generation.0 || slot.active.is_none() {
                return None;
            }
        }
        let slot = &mut self.slots[handle.slot as usize];
        let record = slot.active.take();
        slot.generation += 1;
        self.free.push(handle.slot);
        record
    }

    /// Handles of all in-flight records whose deadline has passed.
    pub fn expired_handles(&self, now_ms: Millis) -> Vec<Handle> {
        self.slots
            .iter()
            .enumerate()
            .filter_map(|(idx, slot)| {
                let active = slot.active.as_ref()?;
                if active.deadline_ms <= now_ms {
                    Some(Handle {
                        slot: idx as u16,
                        generation: Generation(slot.generation),
                    })
                } else {
                    None
                }
            })
            .collect()
    }

    /// Snapshot of every slot for the diagnostics endpoint.
    pub fn snapshot(&self) -> Vec<SlotView> {
        self.slots
            .iter()
            .enumerate()
            .map(|(idx, slot)| match &slot.active {
                Some(active) => SlotView {
                    slot: idx as u16,
                    generation: slot.generation,
                    state: "in_flight",
                    record_id: Some(active.record_id.0),
                    cancel_requested: Some(active.cancel_requested),
                    deadline_ms: Some(active.deadline_ms),
                },
                None => SlotView {
                    slot: idx as u16,
                    generation: slot.generation,
                    state: "free",
                    record_id: None,
                    cancel_requested: None,
                    deadline_ms: None,
                },
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::OpKind;

    fn record(id: u64) -> ActiveRecord {
        ActiveRecord {
            record_id: RecordId(id),
            user_data: id * 100,
            op: OpKind::Nop,
            deadline_ms: 1_000,
            submitted_at_ms: 0,
            cancel_requested: false,
            buffer: None,
        }
    }

    #[test]
    fn reused_slot_gets_new_generation() {
        let mut table = SlotTable::new(1);
        let h1 = table.alloc(record(1)).expect("alloc");
        assert_eq!(h1.generation, Generation(0));
        table.finalize(h1).expect("finalize");
        let h2 = table.alloc(record(2)).expect("re-alloc");
        assert_eq!(h2.slot, h1.slot, "slot is reused");
        assert_eq!(h2.generation, Generation(1), "generation is bumped");
        assert!(table.get_active(h1).is_none(), "old handle is stale");
        assert!(table.get_active(h2).is_some());
    }

    #[test]
    fn full_table_allocates_nothing() {
        let mut table = SlotTable::new(1);
        table.alloc(record(1)).expect("alloc");
        assert!(table.alloc(record(2)).is_none());
        assert_eq!(table.in_flight(), 1);
    }

    #[test]
    fn finalize_rejects_stale_handle() {
        let mut table = SlotTable::new(1);
        let h1 = table.alloc(record(1)).expect("alloc");
        table.finalize(h1).expect("finalize");
        assert!(table.finalize(h1).is_none(), "double finalize rejected");
    }
}
