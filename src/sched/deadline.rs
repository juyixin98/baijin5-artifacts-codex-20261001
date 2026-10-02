//! Deadline scheduler with explainable anti-starvation rules.
//!
//! Dispatch policy, in priority order (first match wins, recorded as the
//! dispatch reason on the event log):
//! 1. **Expired read** — the oldest read whose age >= `read_expire_ns`.
//! 2. **Expired write** — the oldest write whose age >= `write_expire_ns`.
//! 3. **Writes starved** — after `writes_starved` consecutive read dispatches
//!    with a write still pending, force out the closest write.
//! 4. **Closest to head** — greedy seek-distance choice (the "batch" choice).
//!
//! Reads are checked before writes in rule 1/2 because reads are typically
//! latency-critical (a process blocks on them); this mirrors the Linux
//! deadline elevator's read preference and is a deliberate, documented bias.

use super::{Dispatch, DispatchReason, PendingSet, Scheduler};
use crate::request::{BlockRequest, Direction, RequestId};

pub struct DeadlineScheduler {
    pending: PendingSet,
    read_expire_ns: u64,
    write_expire_ns: u64,
    writes_starved: u32,
    reads_in_a_row: u32,
}

impl DeadlineScheduler {
    pub fn new(read_expire_ns: u64, write_expire_ns: u64, writes_starved: u32) -> Self {
        DeadlineScheduler {
            pending: PendingSet::new(),
            read_expire_ns,
            write_expire_ns,
            writes_starved,
            reads_in_a_row: 0,
        }
    }

    fn note_direction(&mut self, dir: Direction) {
        match dir {
            Direction::Read => self.reads_in_a_row += 1,
            Direction::Write => self.reads_in_a_row = 0,
        }
    }
}

impl Scheduler for DeadlineScheduler {
    fn name(&self) -> &'static str {
        "deadline"
    }

    fn insert(&mut self, req: BlockRequest) -> Vec<String> {
        self.pending.insert(req)
    }

    fn remove_member(&mut self, id: &RequestId) -> Option<BlockRequest> {
        self.pending.remove_member(id)
    }

    fn next(&mut self, now_ns: u64, head: u64) -> Option<Dispatch> {
        if self.pending.is_empty() {
            return None;
        }
        // Rule 1: expired read (oldest first).
        if let Some(key) = self
            .pending
            .oldest_expired(Direction::Read, now_ns, self.read_expire_ns)
        {
            let item = self.pending.take(key).expect("expired read");
            self.note_direction(Direction::Read);
            return Some(Dispatch {
                item,
                reason: DispatchReason::DeadlineExpired {
                    direction: Direction::Read,
                },
            });
        }
        // Rule 2: expired write (oldest first).
        if let Some(key) = self
            .pending
            .oldest_expired(Direction::Write, now_ns, self.write_expire_ns)
        {
            let item = self.pending.take(key).expect("expired write");
            self.note_direction(Direction::Write);
            return Some(Dispatch {
                item,
                reason: DispatchReason::DeadlineExpired {
                    direction: Direction::Write,
                },
            });
        }
        // Rule 3: writes starved by a run of reads.
        if self.reads_in_a_row >= self.writes_starved
            && self.pending.has_direction(Direction::Write)
            && self.pending.has_direction(Direction::Read)
        {
            let item = self
                .pending
                .pop_closest_of(head, Direction::Write)
                .expect("write pending");
            self.note_direction(Direction::Write);
            return Some(Dispatch {
                item,
                reason: DispatchReason::DeadlineWritesStarved,
            });
        }
        // Rule 4: greedy closest-to-head.
        let item = self.pending.pop_closest(head).expect("non-empty");
        self.note_direction(item.direction);
        Some(Dispatch {
            item,
            reason: DispatchReason::ClosestToHead,
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

    fn req(id: &str, start: u64, dir: Direction, arrival: u64) -> BlockRequest {
        BlockRequest {
            id: id.to_string(),
            start,
            len: 5,
            direction: dir,
            arrival_ns: arrival,
            deadline_ns: None,
        }
    }

    #[test]
    fn expired_read_beats_closer_fresh_read() {
        let mut s = DeadlineScheduler::new(100, 5000, 2);
        s.insert(req("far_old", 900, Direction::Read, 0));
        s.insert(req("near_new", 10, Direction::Read, 0));
        // At t=50 nothing expired: closest wins.
        let d = s.next(50, 0).expect("near first");
        assert_eq!(d.item.member_ids(), vec!["near_new"]);
        assert_eq!(d.reason, DispatchReason::ClosestToHead);
        // At t=100 the far read is expired and wins despite distance.
        let d = s.next(100, 20).expect("expired far read");
        assert_eq!(d.item.member_ids(), vec!["far_old"]);
        assert_eq!(
            d.reason,
            DispatchReason::DeadlineExpired {
                direction: Direction::Read
            }
        );
    }

    #[test]
    fn writes_starved_forces_write_after_n_reads() {
        // Gapped requests so nothing merges; len 5.
        let mut s = DeadlineScheduler::new(u64::MAX, u64::MAX, 2);
        s.insert(req("w", 1000, Direction::Write, 0));
        s.insert(req("r1", 10, Direction::Read, 0));
        s.insert(req("r2", 20, Direction::Read, 0));
        s.insert(req("r3", 30, Direction::Read, 0));
        assert_eq!(s.next(0, 0).expect("r1").item.member_ids(), vec!["r1"]);
        assert_eq!(s.next(0, 15).expect("r2").item.member_ids(), vec!["r2"]);
        // Two reads in a row with a write pending -> write forced out.
        let d = s.next(0, 25).expect("write forced");
        assert_eq!(d.item.member_ids(), vec!["w"]);
        assert_eq!(d.reason, DispatchReason::DeadlineWritesStarved);
        // Counter reset: back to reads.
        assert_eq!(s.next(0, 1005).expect("r3").item.member_ids(), vec!["r3"]);
    }

    #[test]
    fn expired_read_checked_before_expired_write() {
        let mut s = DeadlineScheduler::new(100, 100, 2);
        s.insert(req("w", 500, Direction::Write, 0));
        s.insert(req("r", 900, Direction::Read, 0));
        let d = s.next(200, 0).expect("read wins tie");
        assert_eq!(d.item.member_ids(), vec!["r"]);
    }
}
