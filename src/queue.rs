//! Bounded submission queue. Overflow is explicit backpressure, never a
//! silent drop or an unbounded growth.

use std::collections::VecDeque;

use crate::model::SubmissionId;

#[derive(Debug)]
pub struct SubmissionQueue {
    capacity: usize,
    inner: VecDeque<SubmissionId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QueueFull {
    pub capacity: usize,
}

impl SubmissionQueue {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity,
            inner: VecDeque::with_capacity(capacity),
        }
    }

    pub fn is_full(&self) -> bool {
        self.inner.len() >= self.capacity
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn push_back(&mut self, id: SubmissionId) -> Result<(), QueueFull> {
        if self.is_full() {
            return Err(QueueFull {
                capacity: self.capacity,
            });
        }
        self.inner.push_back(id);
        Ok(())
    }

    pub fn front(&self) -> Option<SubmissionId> {
        self.inner.front().copied()
    }

    pub fn pop_front(&mut self) -> Option<SubmissionId> {
        self.inner.pop_front()
    }

    /// Remove a specific id (used when a queued submission is cancelled
    /// before dispatch). Returns true if it was present.
    pub fn remove(&mut self, id: SubmissionId) -> bool {
        let before = self.inner.len();
        self.inner.retain(|x| *x != id);
        self.inner.len() != before
    }
}
