//! Scheduler trait, shared pending-set (with adjacency merging and member
//! removal), and dispatch-reason tagging for explainable logs.

pub mod deadline;
pub mod scan;

use crate::request::{BlockRequest, Direction, QueuedItem, RequestId};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Why the scheduler picked this item. Recorded on every `dispatched` event.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum DispatchReason {
    /// SCAN/elevator sweep in the given direction ("up" = toward higher sectors).
    ScanSweep { direction: String },
    /// Deadline scheduler: oldest request of this class exceeded its expire time.
    DeadlineExpired { direction: Direction },
    /// Deadline scheduler: reads dispatched `writes_starved` times in a row
    /// while a write was pending, so a write was forced out.
    DeadlineWritesStarved,
    /// No anti-starvation rule fired; closest-to-head request chosen.
    ClosestToHead,
}

pub struct Dispatch {
    pub item: QueuedItem,
    pub reason: DispatchReason,
}

pub trait Scheduler {
    fn name(&self) -> &'static str;
    /// Insert a request, merging with adjacent same-direction pending items.
    /// Returns human-readable merge notes for the event log.
    fn insert(&mut self, req: BlockRequest) -> Vec<String>;
    /// Remove one member request (for cancel-before-dispatch). If the member
    /// sits inside a merged item, the item is split back into contiguous runs.
    /// Returns the removed request, or None if not pending.
    fn remove_member(&mut self, id: &RequestId) -> Option<BlockRequest>;
    /// Pick the next item to dispatch at time `now_ns` with head at `head`.
    fn next(&mut self, now_ns: u64, head: u64) -> Option<Dispatch>;
    fn has_pending(&self) -> bool;
    fn pending_count(&self) -> usize;
}

/// Keyed by `(start, seq)` so ordering is total and deterministic.
type Key = (u64, u64);

/// The set of queued items shared by both schedulers. Owns all merge/split
/// mechanics so the schedulers themselves only encode *choice* policy.
#[derive(Default)]
pub struct PendingSet {
    items: BTreeMap<Key, QueuedItem>,
    next_seq: u64,
}

impl PendingSet {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    pub fn has_direction(&self, dir: Direction) -> bool {
        self.items.values().any(|i| i.direction == dir)
    }

    fn alloc_seq(&mut self) -> u64 {
        let s = self.next_seq;
        self.next_seq += 1;
        s
    }

    /// Insert with merging. A new request can merge with the item ending at
    /// its start, the item starting at its end, or both (three-way merge).
    pub fn insert(&mut self, req: BlockRequest) -> Vec<String> {
        let mut notes = Vec::new();
        let pred_key = self
            .items
            .iter()
            .find(|(_, it)| it.direction == req.direction && it.end() == req.start)
            .map(|(k, _)| *k);
        let succ_key = self
            .items
            .iter()
            .find(|(_, it)| it.direction == req.direction && it.start == req.end())
            .map(|(k, _)| *k);

        match (pred_key, succ_key) {
            (Some(pk), Some(sk)) => {
                let succ = self.items.remove(&sk).expect("succ key");
                let pred = self.items.get_mut(&pk).expect("pred key");
                let merged_ids = pred.member_ids();
                // Order matters: absorb the request first so pred's range
                // reaches the successor, then absorb the successor.
                pred.absorb(req.clone());
                pred.absorb_item(succ);
                notes.push(format!(
                    "request {} merged three-way into item covering members {:?}",
                    req.id, merged_ids
                ));
            }
            (Some(pk), None) => {
                let pred = self.items.get_mut(&pk).expect("pred key");
                let merged_ids = pred.member_ids();
                pred.absorb(req.clone());
                notes.push(format!(
                    "request {} merged into item covering members {:?}",
                    req.id, merged_ids
                ));
            }
            (None, Some(sk)) => {
                let succ = self.items.get_mut(&sk).expect("succ key");
                let merged_ids = succ.member_ids();
                succ.absorb(req.clone());
                notes.push(format!(
                    "request {} merged into item covering members {:?}",
                    req.id, merged_ids
                ));
            }
            (None, None) => {
                let seq = self.alloc_seq();
                self.items.insert((req.start, seq), QueuedItem::new(req, seq));
            }
        }
        // Merging may have changed an item's start, which is its map key.
        self.rekey();
        notes
    }

    /// Rebuild keys after in-place merges changed `start` values.
    fn rekey(&mut self) {
        let old = std::mem::take(&mut self.items);
        self.items = old.into_iter().map(|(_, it)| ((it.start, it.seq), it)).collect();
    }

    /// Remove a single member by id. Splits merged items back into contiguous
    /// runs so remaining members keep their identities and ranges.
    pub fn remove_member(&mut self, id: &RequestId) -> Option<BlockRequest> {
        let key = self
            .items
            .iter()
            .find(|(_, it)| it.members.iter().any(|m| &m.id == id))
            .map(|(k, _)| *k)?;
        let mut item = self.items.remove(&key).expect("key found");
        let pos = item.members.iter().position(|m| &m.id == id).expect("member");
        let removed = item.members.remove(pos);
        if !item.members.is_empty() {
            // Re-split remaining members into contiguous runs.
            let mut runs: Vec<QueuedItem> = Vec::new();
            for m in item.members {
                match runs.last_mut() {
                    Some(last) if last.adjacent(&m) => last.absorb(m),
                    _ => runs.push(QueuedItem::new(m, self.alloc_seq())),
                }
            }
            for run in runs {
                self.items.insert((run.start, run.seq), run);
            }
        }
        Some(removed)
    }

    pub fn take(&mut self, key: Key) -> Option<QueuedItem> {
        self.items.remove(&key)
    }

    /// Key of the item with the smallest `first_arrival_ns` of direction `dir`
    /// whose age at `now_ns` is >= `expire_ns`.
    pub fn oldest_expired(&self, dir: Direction, now_ns: u64, expire_ns: u64) -> Option<Key> {
        self.items
            .iter()
            .filter(|(_, it)| it.direction == dir)
            .filter(|(_, it)| now_ns.saturating_sub(it.first_arrival_ns) >= expire_ns)
            .min_by_key(|(_, it)| (it.first_arrival_ns, it.seq))
            .map(|(k, _)| *k)
    }

    /// Pop the item whose start is closest to `head` (ties: lower start, then seq).
    pub fn pop_closest(&mut self, head: u64) -> Option<QueuedItem> {
        let key = self
            .items
            .iter()
            .min_by_key(|(_, it)| (it.start.abs_diff(head), it.start, it.seq))
            .map(|(k, _)| *k)?;
        self.items.remove(&key)
    }

    /// Pop the closest-to-head item among those with direction `dir`.
    pub fn pop_closest_of(&mut self, head: u64, dir: Direction) -> Option<QueuedItem> {
        let key = self
            .items
            .iter()
            .filter(|(_, it)| it.direction == dir)
            .min_by_key(|(_, it)| (it.start.abs_diff(head), it.start, it.seq))
            .map(|(k, _)| *k)?;
        self.items.remove(&key)
    }

    /// Pop the lowest-start item with `start >= head` (SCAN upward step).
    pub fn pop_first_at_or_above(&mut self, head: u64) -> Option<QueuedItem> {
        let key = self
            .items
            .range((head, 0)..)
            .next()
            .map(|(k, _)| *k)?;
        self.items.remove(&key)
    }

    /// Pop the highest-start item with `start <= head` (SCAN downward step).
    pub fn pop_last_at_or_below(&mut self, head: u64) -> Option<QueuedItem> {
        let key = self
            .items
            .range(..=(head, u64::MAX))
            .next_back()
            .map(|(k, _)| *k)?;
        self.items.remove(&key)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::request::Direction;

    fn req(id: &str, start: u64, len: u64) -> BlockRequest {
        BlockRequest {
            id: id.to_string(),
            start,
            len,
            direction: Direction::Read,
            arrival_ns: 0,
            deadline_ns: None,
        }
    }

    #[test]
    fn three_way_merge_preserves_all_member_identities() {
        let mut set = PendingSet::new();
        set.insert(req("m1", 100, 10));
        set.insert(req("m2", 110, 10));
        set.insert(req("m3", 130, 10));
        assert_eq!(set.len(), 2); // [100,120) and [130,140)
        set.insert(req("m4", 120, 10)); // bridges the gap
        assert_eq!(set.len(), 1);
        let item = set.pop_closest(0).expect("one item");
        assert_eq!((item.start, item.len), (100, 40));
        assert_eq!(item.member_ids(), vec!["m1", "m2", "m4", "m3"]);
    }

    #[test]
    fn remove_interior_member_splits_item_into_contiguous_runs() {
        let mut set = PendingSet::new();
        set.insert(req("m1", 100, 10));
        set.insert(req("m2", 110, 10));
        set.insert(req("m3", 120, 10));
        assert_eq!(set.len(), 1);
        let removed = set.remove_member(&"m2".to_string()).expect("m2 pending");
        assert_eq!(removed.id, "m2");
        assert_eq!(set.len(), 2);
        let first = set.pop_closest(0).expect("run 1");
        assert_eq!(first.member_ids(), vec!["m1"]);
        let second = set.pop_closest(0).expect("run 2");
        assert_eq!(second.member_ids(), vec!["m3"]);
    }

    #[test]
    fn remove_unknown_member_returns_none() {
        let mut set = PendingSet::new();
        set.insert(req("m1", 100, 10));
        assert!(set.remove_member(&"ghost".to_string()).is_none());
    }

    #[test]
    fn oldest_expired_picks_earliest_arrival_of_direction() {
        let mut set = PendingSet::new();
        let mut r1 = req("r1", 10, 5);
        r1.arrival_ns = 100;
        let mut w1 = req("w1", 20, 5);
        w1.direction = Direction::Write;
        w1.arrival_ns = 50;
        set.insert(r1);
        set.insert(w1);
        // At t=160 with expire=100: read age 60 (no), write age 110 (yes).
        let key = set
            .oldest_expired(Direction::Write, 160, 100)
            .expect("write expired");
        assert_eq!(set.take(key).expect("take").member_ids(), vec!["w1"]);
        assert!(set.oldest_expired(Direction::Read, 160, 100).is_none());
    }
}
