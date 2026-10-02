//! Runqueue: runnable tasks ordered by (vruntime, insertion sequence).
//! Backed by a BTreeMap, the std ordered-map analogue of CFS's rbtree.

use std::collections::BTreeMap;

#[derive(Debug, Default)]
pub struct RunQueue {
    /// (vruntime, seq) -> task index. `seq` breaks ties deterministically in
    /// arrival order so equal-vruntime tasks round-robin instead of starving.
    entries: BTreeMap<(u64, u64), usize>,
    seq: u64,
    total_weight: u64,
}

impl RunQueue {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&mut self, task_id: usize, vruntime: u64, weight: u64) {
        let key = (vruntime, self.seq);
        self.seq += 1;
        self.entries.insert(key, task_id);
        self.total_weight += weight;
    }

    /// The leftmost (minimum-vruntime) task without removing it.
    pub fn peek_min(&self) -> Option<(usize, u64)> {
        self.entries
            .first_key_value()
            .map(|(&(vruntime, _), &id)| (id, vruntime))
    }

    pub fn pop_min(&mut self) -> Option<(usize, u64)> {
        let (key, id) = self.entries.pop_first()?;
        Some((id, key.0))
    }

    pub fn remove_weight(&mut self, weight: u64) {
        self.total_weight = self.total_weight.saturating_sub(weight);
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn total_weight(&self) -> u64 {
        self.total_weight
    }
}
