//! Buffer registry: the runtime's resource algorithm.
//!
//! Every submission whose operation moves data leases exactly one buffer for
//! its whole lifetime. The lease is returned to the pool exactly once, when
//! the owning record finalizes. `release_all` — used at shutdown — refuses
//! to run while any lease is outstanding, which is how "resource release
//! waits for all associated completions" is enforced.
//!
//! All transitions are checked; an impossible transition (e.g. completing a
//! lease twice) is a typed error, never a silent no-op, so a double-free
//! bug in the core shows up as a testable failure instead of corruption.

use crate::model::RecordId;
use serde::Serialize;

/// Identifier of one buffer in the pool.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
pub struct BufferId(pub u16);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BufferState {
    Free,
    Leased(RecordId),
    /// Pool has been released for shutdown; nothing may be leased again.
    Released,
}

/// Errors the registry can report. These are the failure *categories* the
/// resource algorithm distinguishes.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum ResourceError {
    #[error("no free buffer available")]
    Exhausted,
    #[error("buffer {0:?} is not leased")]
    NotLeased(BufferId),
    #[error("buffer {0:?} lease completed twice (double free)")]
    DoubleFree(BufferId),
    #[error("buffer pool already released")]
    AlreadyReleased,
    #[error("release blocked: {0:?} lease(s) still outstanding")]
    OutstandingLeases(Vec<RecordId>),
}

#[derive(Debug)]
pub struct BufferRegistry {
    states: Vec<BufferState>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct BufferStats {
    pub total: usize,
    pub free: usize,
    pub leased: usize,
    pub released: bool,
}

impl BufferRegistry {
    pub fn new(size: usize) -> Self {
        Self {
            states: vec![BufferState::Free; size],
        }
    }

    /// Lease one buffer for `record_id`.
    pub fn lease(&mut self, record_id: RecordId) -> Result<BufferId, ResourceError> {
        if self.states.iter().all(|s| *s == BufferState::Released) {
            return Err(ResourceError::AlreadyReleased);
        }
        match self.states.iter().position(|s| *s == BufferState::Free) {
            Some(idx) => {
                self.states[idx] = BufferState::Leased(record_id);
                Ok(BufferId(idx as u16))
            }
            None => Err(ResourceError::Exhausted),
        }
    }

    /// Return a lease to the pool. Called exactly once per lease, when the
    /// owning record finalizes.
    pub fn complete(&mut self, id: BufferId) -> Result<(), ResourceError> {
        let slot = self
            .states
            .get_mut(id.0 as usize)
            .ok_or(ResourceError::NotLeased(id))?;
        match slot {
            BufferState::Leased(_) => {
                *slot = BufferState::Free;
                Ok(())
            }
            BufferState::Free => Err(ResourceError::DoubleFree(id)),
            BufferState::Released => Err(ResourceError::AlreadyReleased),
        }
    }

    /// Record ids currently holding a lease.
    pub fn outstanding(&self) -> Vec<RecordId> {
        self.states
            .iter()
            .filter_map(|s| match s {
                BufferState::Leased(id) => Some(*id),
                _ => None,
            })
            .collect()
    }

    /// Shutdown transition. Succeeds only when every lease has completed —
    /// i.e. every associated completion has been processed.
    pub fn release_all(&mut self) -> Result<(), ResourceError> {
        let outstanding = self.outstanding();
        if !outstanding.is_empty() {
            return Err(ResourceError::OutstandingLeases(outstanding));
        }
        for s in &mut self.states {
            *s = BufferState::Released;
        }
        Ok(())
    }

    pub fn stats(&self) -> BufferStats {
        let free = self
            .states
            .iter()
            .filter(|s| **s == BufferState::Free)
            .count();
        let leased = self
            .states
            .iter()
            .filter(|s| matches!(s, BufferState::Leased(_)))
            .count();
        let released = self.states.iter().all(|s| *s == BufferState::Released);
        BufferStats {
            total: self.states.len(),
            free,
            leased,
            released,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lease_complete_cycle() {
        let mut reg = BufferRegistry::new(1);
        let id = reg.lease(RecordId(1)).expect("first lease");
        assert_eq!(reg.stats().leased, 1);
        reg.complete(id).expect("complete once");
        assert_eq!(reg.stats().free, 1);
    }

    #[test]
    fn double_complete_is_double_free_error() {
        let mut reg = BufferRegistry::new(1);
        let id = reg.lease(RecordId(1)).expect("lease");
        reg.complete(id).expect("first complete");
        assert_eq!(reg.complete(id), Err(ResourceError::DoubleFree(id)));
    }

    #[test]
    fn exhausted_pool_reports_category() {
        let mut reg = BufferRegistry::new(1);
        reg.lease(RecordId(1)).expect("lease");
        assert_eq!(reg.lease(RecordId(2)), Err(ResourceError::Exhausted));
    }

    #[test]
    fn release_waits_for_outstanding_leases() {
        let mut reg = BufferRegistry::new(2);
        let a = reg.lease(RecordId(10)).expect("lease a");
        let b = reg.lease(RecordId(11)).expect("lease b");
        assert_eq!(
            reg.release_all(),
            Err(ResourceError::OutstandingLeases(vec![RecordId(10), RecordId(11)]))
        );
        reg.complete(a).expect("complete a");
        assert_eq!(
            reg.release_all(),
            Err(ResourceError::OutstandingLeases(vec![RecordId(11)]))
        );
        reg.complete(b).expect("complete b");
        reg.release_all().expect("release after all completions");
        assert!(reg.stats().released);
        assert_eq!(reg.lease(RecordId(12)), Err(ResourceError::AlreadyReleased));
    }
}
