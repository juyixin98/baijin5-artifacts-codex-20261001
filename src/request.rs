//! Block request identity and the queued (possibly merged) item type.
//!
//! BOUNDARY: a request is a half-open logical sector range `[start, start+len)`
//! with an explicit `direction`. Merging never destroys identity: a
//! [`QueuedItem`] keeps every original [`BlockRequest`] in `members`, and each
//! member gets its own completion outcome.

use serde::{Deserialize, Serialize};

pub type RequestId = String;

#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    Read,
    Write,
}

impl Direction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Direction::Read => "read",
            Direction::Write => "write",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BlockRequest {
    pub id: RequestId,
    /// First logical sector (inclusive).
    pub start: u64,
    /// Number of sectors; must be > 0. Range is `[start, start+len)`.
    pub len: u64,
    pub direction: Direction,
    pub arrival_ns: u64,
    /// Deadline budget relative to arrival (ns): the request misses its
    /// deadline iff `finish_ns > arrival_ns + deadline_ns`. Used for miss
    /// *accounting* only; the deadline scheduler's anti-starvation uses its
    /// own fixed expire parameters.
    #[serde(default)]
    pub deadline_ns: Option<u64>,
}

impl BlockRequest {
    /// Exclusive end sector.
    pub fn end(&self) -> u64 {
        self.start + self.len
    }
}

/// Terminal state of a request.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FinalStatus {
    /// Serviced to completion by the device model.
    Completed,
    /// Cancelled while still queued: no device time was spent, cost is 0.
    CancelledBeforeDispatch,
    /// Cancel arrived after dispatch: the device operation cannot be recalled,
    /// so it runs to its normal finish and the cost IS counted. Data integrity
    /// of the result is uncertain (reported under `uncertain` in summaries).
    CancelledAfterDispatch,
}

/// A schedulable unit: one request, or several adjacent same-direction
/// requests merged into a single range. Original identities are preserved in
/// `members` (sorted by start sector).
#[derive(Clone, Debug)]
pub struct QueuedItem {
    pub start: u64,
    pub len: u64,
    pub direction: Direction,
    pub members: Vec<BlockRequest>,
    /// Earliest arrival among members; drives deadline-scheduler expiry.
    pub first_arrival_ns: u64,
    /// Insertion sequence, used as a deterministic tie-break key.
    pub seq: u64,
}

impl QueuedItem {
    pub fn new(req: BlockRequest, seq: u64) -> Self {
        QueuedItem {
            start: req.start,
            len: req.len,
            direction: req.direction,
            first_arrival_ns: req.arrival_ns,
            members: vec![req],
            seq,
        }
    }

    pub fn end(&self) -> u64 {
        self.start + self.len
    }

    pub fn member_ids(&self) -> Vec<RequestId> {
        self.members.iter().map(|m| m.id.clone()).collect()
    }

    /// True iff `req` has the same direction and is exactly adjacent
    /// (contiguous, not overlapping) to this item's range.
    pub fn adjacent(&self, req: &BlockRequest) -> bool {
        self.direction == req.direction && (req.start == self.end() || req.end() == self.start)
    }

    /// Absorb `req`, which must satisfy [`QueuedItem::adjacent`].
    pub fn absorb(&mut self, req: BlockRequest) {
        debug_assert!(self.adjacent(&req));
        let new_start = self.start.min(req.start);
        let new_end = self.end().max(req.end());
        self.start = new_start;
        self.len = new_end - new_start;
        self.first_arrival_ns = self.first_arrival_ns.min(req.arrival_ns);
        self.members.push(req);
        self.members.sort_by_key(|m| m.start);
    }

    /// Absorb another item (its range must be exactly adjacent to ours).
    pub fn absorb_item(&mut self, other: QueuedItem) {
        debug_assert_eq!(self.direction, other.direction);
        debug_assert!(other.start == self.end() || other.end() == self.start);
        let new_start = self.start.min(other.start);
        let new_end = self.end().max(other.end());
        self.start = new_start;
        self.len = new_end - new_start;
        self.first_arrival_ns = self.first_arrival_ns.min(other.first_arrival_ns);
        self.seq = self.seq.min(other.seq);
        self.members.extend(other.members);
        self.members.sort_by_key(|m| m.start);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn absorb_extends_range_and_keeps_members_sorted() {
        let mut item = QueuedItem::new(req("a", 100, 10), 0);
        item.absorb(req("b", 110, 10));
        item.absorb(req("c", 90, 10));
        assert_eq!((item.start, item.len), (90, 30));
        assert_eq!(item.member_ids(), vec!["c", "a", "b"]);
    }

    #[test]
    fn adjacency_rejects_overlap_gap_and_direction_mismatch() {
        let item = QueuedItem::new(req("a", 100, 10), 0);
        assert!(item.adjacent(&req("b", 110, 5)));
        assert!(item.adjacent(&req("b", 95, 5)));
        assert!(!item.adjacent(&req("b", 105, 5))); // overlap
        assert!(!item.adjacent(&req("b", 111, 5))); // gap
        let mut w = req("b", 110, 5);
        w.direction = Direction::Write;
        assert!(!item.adjacent(&w)); // direction mismatch
    }
}
