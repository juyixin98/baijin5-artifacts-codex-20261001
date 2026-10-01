//! Pull-based batch source.
//!
//! The total number of rows is **unknown** while reading: a source yields
//! batches until it returns `Ok(None)`. The operator is required to drain a
//! source all the way to `None` — stopping early would silently drop rows when
//! a LIMIT appears "satisfied", because ties, offsets and global ordering all
//! depend on rows that have not been seen yet.

use sl_types::{Batch, Result};

/// A source of typed batches. Implementations must be deterministic for a
/// given construction (fixtures are); sources may fail, in which case the
/// error propagates with category `source`.
pub trait BatchSource {
    /// Produce the next batch, or `None` at end of stream.
    fn next_batch(&mut self) -> Result<Option<Batch>>;

    /// Human-readable location of this source, used in correlated logs
    /// (e.g. `fixture:synth/ties.json`).
    fn location(&self) -> &str {
        "anonymous-source"
    }
}

/// Source backed by an in-memory list of batches. Also records how often it
/// was pulled so tests can prove the operator drained it to completion.
#[derive(Debug)]
pub struct VecSource {
    batches: std::collections::VecDeque<Batch>,
    pulls: usize,
    ended: bool,
    location: String,
}

impl VecSource {
    pub fn new(batches: Vec<Batch>) -> Self {
        Self {
            batches: batches.into(),
            pulls: 0,
            ended: false,
            location: "in-memory-vec-source".to_string(),
        }
    }

    pub fn named(mut self, location: impl Into<String>) -> Self {
        self.location = location.into();
        self
    }

    pub fn pull_count(&self) -> usize {
        self.pulls
    }

    pub fn was_drained(&self) -> bool {
        self.ended
    }
}

impl BatchSource for VecSource {
    fn next_batch(&mut self) -> Result<Option<Batch>> {
        self.pulls += 1;
        let next = self.batches.pop_front();
        if next.is_none() {
            self.ended = true;
        }
        Ok(next)
    }

    fn location(&self) -> &str {
        &self.location
    }
}

/// Source that injects an error after `succeed_before` batches. Lets tests
/// assert the `source` failure category and prove partial state is discarded.
pub struct FailingSource {
    inner: VecSource,
    succeed_before: usize,
    delivered: usize,
}

impl FailingSource {
    pub fn new(batches: Vec<Batch>, succeed_before: usize) -> Self {
        Self {
            inner: VecSource::new(batches),
            succeed_before,
            delivered: 0,
        }
    }
}

impl BatchSource for FailingSource {
    fn next_batch(&mut self) -> Result<Option<Batch>> {
        if self.delivered >= self.succeed_before {
            return Err(sl_types::SlError::new(
                sl_types::ErrorCategory::Source,
                "synthetic_source_failure",
                format!(
                    "synthetic source failed after {} batches (injected by FailingSource)",
                    self.delivered
                ),
                "sl_operator::source::failing",
            ));
        }
        match self.inner.next_batch()? {
            Some(b) => {
                self.delivered += 1;
                Ok(Some(b))
            }
            None => Ok(None),
        }
    }

    fn location(&self) -> &str {
        "failing-synthetic-source"
    }
}
