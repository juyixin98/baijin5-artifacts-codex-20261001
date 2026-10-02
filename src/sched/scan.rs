//! SCAN (elevator) scheduler: sweep in one direction serving requests in
//! sector order; reverse when the sweep empties. Ignores deadlines entirely —
//! that indifference is exactly what the comparison measures.

use super::{Dispatch, DispatchReason, PendingSet, Scheduler};
use crate::request::{BlockRequest, RequestId};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Sweep {
    Up,
    Down,
}

impl Sweep {
    fn as_str(&self) -> &'static str {
        match self {
            Sweep::Up => "up",
            Sweep::Down => "down",
        }
    }
}

pub struct ScanScheduler {
    pending: PendingSet,
    direction: Sweep,
}

impl ScanScheduler {
    /// `initial_up`: start sweeping toward higher sectors (configurable).
    pub fn new(initial_up: bool) -> Self {
        ScanScheduler {
            pending: PendingSet::new(),
            direction: if initial_up { Sweep::Up } else { Sweep::Down },
        }
    }
}

impl Scheduler for ScanScheduler {
    fn name(&self) -> &'static str {
        "scan"
    }

    fn insert(&mut self, req: BlockRequest) -> Vec<String> {
        self.pending.insert(req)
    }

    fn remove_member(&mut self, id: &RequestId) -> Option<BlockRequest> {
        self.pending.remove_member(id)
    }

    fn next(&mut self, _now_ns: u64, head: u64) -> Option<Dispatch> {
        let item = match self.direction {
            Sweep::Up => self.pending.pop_first_at_or_above(head).or_else(|| {
                self.direction = Sweep::Down;
                self.pending.pop_last_at_or_below(head)
            }),
            Sweep::Down => self.pending.pop_last_at_or_below(head).or_else(|| {
                self.direction = Sweep::Up;
                self.pending.pop_first_at_or_above(head)
            }),
        }?;
        Some(Dispatch {
            item,
            reason: DispatchReason::ScanSweep {
                direction: self.direction.as_str().to_string(),
            },
        })
    }

    fn has_pending(&self) -> bool {
        !self.pending.is_empty()
    }

    fn pending_count(&self) -> usize {
        self.pending.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::request::Direction;
    use crate::sched::Scheduler;

    fn req(id: &str, start: u64) -> BlockRequest {
        BlockRequest {
            id: id.to_string(),
            start,
            len: 10,
            direction: Direction::Read,
            arrival_ns: 0,
            deadline_ns: None,
        }
    }

    #[test]
    fn sweeps_up_then_reverses() {
        let mut s = ScanScheduler::new(true);
        s.insert(req("a", 100));
        s.insert(req("b", 50));
        s.insert(req("c", 10));
        // head at 60: up-sweep takes a(100), then nothing above -> reverse, b(50), c(10).
        let d1 = s.next(0, 60).expect("a");
        assert_eq!(d1.item.member_ids(), vec!["a"]);
        assert_eq!(
            d1.reason,
            DispatchReason::ScanSweep { direction: "up".to_string() }
        );
        let d2 = s.next(0, 110).expect("b");
        assert_eq!(d2.item.member_ids(), vec!["b"]);
        assert_eq!(
            d2.reason,
            DispatchReason::ScanSweep { direction: "down".to_string() }
        );
        let d3 = s.next(0, 50).expect("c");
        assert_eq!(d3.item.member_ids(), vec!["c"]);
        assert!(s.next(0, 0).is_none());
    }
}
