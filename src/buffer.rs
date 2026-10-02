//! Buffer lease registry.
//!
//! Every dispatched IO leases exactly one buffer. The buffer returns to the
//! pool only when the device hands it back via a completion — a timeout
//! finalizes the record but does *not* release the buffer, because the
//! device may still be writing into it. Release is owner-checked and
//! double-release is detected and rejected, never applied twice.

use crate::model::{BufferId, SubmissionId};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Slot {
    Leased { owner: SubmissionId },
    /// Tombstone kept so a second release of the same lease is detected.
    Released { owner: SubmissionId },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BufferError {
    /// Pool exhausted: resource-level backpressure, dispatch is deferred.
    Exhausted,
    Unknown(BufferId),
    DoubleRelease { id: BufferId },
    OwnerMismatch {
        id: BufferId,
        expected: SubmissionId,
        found: SubmissionId,
    },
}

impl std::fmt::Display for BufferError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BufferError::Exhausted => write!(f, "buffer pool exhausted"),
            BufferError::Unknown(id) => write!(f, "unknown buffer {id}"),
            BufferError::DoubleRelease { id } => {
                write!(f, "buffer {id} released twice (double-free attempt rejected)")
            }
            BufferError::OwnerMismatch {
                id,
                expected,
                found,
            } => write!(
                f,
                "buffer {id} owned by submission {expected}, release attempted by {found}"
            ),
        }
    }
}

#[derive(Debug)]
pub struct BufferRegistry {
    capacity: usize,
    slots: Vec<Slot>,
    leased: usize,
}

impl BufferRegistry {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity,
            slots: Vec::new(),
            leased: 0,
        }
    }

    pub fn lease(&mut self, owner: SubmissionId) -> Result<BufferId, BufferError> {
        if let Some(idx) = self
            .slots
            .iter()
            .position(|s| !matches!(s, Slot::Leased { .. }))
        {
            self.slots[idx] = Slot::Leased { owner };
            self.leased += 1;
            return Ok(BufferId(idx as u64));
        }
        if self.slots.len() < self.capacity {
            self.slots.push(Slot::Leased { owner });
            self.leased += 1;
            return Ok(BufferId((self.slots.len() - 1) as u64));
        }
        Err(BufferError::Exhausted)
    }

    pub fn release(&mut self, id: BufferId, owner: SubmissionId) -> Result<(), BufferError> {
        let slot = self
            .slots
            .get_mut(id.0 as usize)
            .ok_or(BufferError::Unknown(id))?;
        match *slot {
            Slot::Leased { owner: o } if o == owner => {
                *slot = Slot::Released { owner };
                self.leased -= 1;
                Ok(())
            }
            Slot::Leased { owner: o } => Err(BufferError::OwnerMismatch {
                id,
                expected: o,
                found: owner,
            }),
            Slot::Released { .. } => Err(BufferError::DoubleRelease { id }),
        }
    }

    pub fn leased_count(&self) -> usize {
        self.leased
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }
}
