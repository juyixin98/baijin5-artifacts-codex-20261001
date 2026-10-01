//! The comparator and retained heap entry.
//!
//! Two distinct orderings live here on purpose:
//!
//! * **Full ordering** — user sort keys lexicographically (each with its own
//!   direction and NULL placement), with the stable row identity as the final
//!   tie-breaker. This is a total order: no two distinct rows compare equal.
//! * **User-key equality** — equality on the user sort keys alone, ignoring
//!   identity. WITH TIES expands the retained set using *this* relation, never
//!   identity. Two rows with identical sort keys are ties even though the
//!   full order separates them.

use std::cmp::Ordering;

use sl_types::query::{Direction, NullOrder, OrderKey};
use sl_types::schema::RelationSchema;
use sl_types::{null_aware_cmp, Scalar, SlError};

/// An ORDER BY key resolved against a schema: a physical column position plus
/// its ordering directives.
#[derive(Debug, Clone)]
pub struct ResolvedKey {
    pub column_index: usize,
    pub column_name: String,
    pub direction: Direction,
    pub nulls: NullOrder,
}

impl ResolvedKey {
    pub fn resolve_all(
        schema: &RelationSchema,
        keys: &[OrderKey],
    ) -> Result<Vec<ResolvedKey>, SlError> {
        keys.iter()
            .map(|k| {
                let column_index = schema.index_of(&k.column)?;
                Ok(ResolvedKey {
                    column_index,
                    column_name: k.column.clone(),
                    direction: k.direction,
                    nulls: k.nulls,
                })
            })
            .collect()
    }
}

/// Compare two rows under one resolved key.
#[inline]
pub fn compare_one(left: &[Scalar], right: &[Scalar], key: &ResolvedKey) -> Ordering {
    let a = &left[key.column_index];
    let b = &right[key.column_index];
    let nulls_first = key.nulls == NullOrder::First;
    let ord = null_aware_cmp(a, b, nulls_first);
    match key.direction {
        // NULL placement is independent of direction: null_aware_cmp already
        // placed the NULL; only the non-null value order is reversed.
        Direction::Asc => ord,
        Direction::Desc => {
            if ord == Ordering::Equal {
                Ordering::Equal
            } else {
                ord.reverse()
            }
        }
    }
}

/// Full ordering over `(user keys ..., identity)`. Total for distinct rows.
#[inline]
pub fn compare_full(left: &Entry, right: &Entry, keys: &[ResolvedKey]) -> Ordering {
    match compare_user_keys(left, right, keys) {
        Ordering::Equal => {
            // Stable tie-break: original position, ascending, ALWAYS —
            // independent of any user direction. Never participates in
            // WITH TIES.
            left.identity.cmp(&right.identity)
        }
        other => other,
    }
}

/// User-key relation only (no identity). `Equal` means "is a tie".
#[inline]
pub fn compare_user_keys(left: &Entry, right: &Entry, keys: &[ResolvedKey]) -> Ordering {
    for key in keys {
        let ord = compare_one(&left.row, &right.row, key);
        if ord != Ordering::Equal {
            return ord;
        }
    }
    Ordering::Equal
}

/// One row retained by the streaming selector.
#[derive(Debug, Clone)]
pub struct Entry {
    /// Full row in schema column order.
    pub row: Vec<Scalar>,
    /// Original global position across the whole source; stable identity.
    pub identity: u64,
}

/// Approximate retained payload bytes (for budget accounting).
pub fn entry_payload_bytes(e: &Entry) -> usize {
    e.row.iter().map(Scalar::estimated_bytes).sum()
}

/// A bounded max-heap whose root is the *worst* retained row under the full
/// ordering (the row with the greatest rank — the next eviction candidate).
///
/// Implemented as a classic binary heap over a `Vec` because `Ord` cannot
/// carry the resolved-key context; the comparator is passed explicitly, which
/// also makes ASC/DESC/NULL behaviour directly testable.
pub struct WorstHeap {
    items: Vec<Entry>,
    keys: Vec<ResolvedKey>,
}

impl WorstHeap {
    pub fn new(keys: Vec<ResolvedKey>) -> Self {
        Self {
            items: Vec::new(),
            keys,
        }
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    pub fn keys(&self) -> &[ResolvedKey] {
        &self.keys
    }

    /// "worse" = greater under the full ordering (sorted last, first to evict).
    fn worse(&self, a: &Entry, b: &Entry) -> bool {
        compare_full(a, b, &self.keys) == Ordering::Greater
    }

    pub fn push(&mut self, entry: Entry) {
        self.items.push(entry);
        let last = self.items.len() - 1;
        self.sift_up(last);
    }

    pub fn peek(&self) -> Option<&Entry> {
        self.items.first()
    }

    pub fn pop(&mut self) -> Option<Entry> {
        if self.items.is_empty() {
            return None;
        }
        let last = self.items.len() - 1;
        self.items.swap(0, last);
        let out = self.items.pop();
        if !self.items.is_empty() {
            self.sift_down(0);
        }
        out
    }

    pub fn clear(&mut self) {
        self.items.clear();
    }

    /// Drain all entries in ascending full order (best first).
    pub fn into_sorted(mut self) -> Vec<Entry> {
        let mut out = Vec::with_capacity(self.items.len());
        while let Some(e) = self.pop() {
            out.push(e);
        }
        out
    }

    fn sift_up(&mut self, mut idx: usize) {
        while idx > 0 {
            let parent = (idx - 1) / 2;
            if self.worse(&self.items[idx], &self.items[parent]) {
                self.items.swap(idx, parent);
                idx = parent;
            } else {
                break;
            }
        }
    }

    fn sift_down(&mut self, mut idx: usize) {
        let n = self.items.len();
        loop {
            let left = 2 * idx + 1;
            let right = 2 * idx + 2;
            let mut worst = idx;
            if left < n && self.worse(&self.items[left], &self.items[worst]) {
                worst = left;
            }
            if right < n && self.worse(&self.items[right], &self.items[worst]) {
                worst = right;
            }
            if worst == idx {
                break;
            }
            self.items.swap(idx, worst);
            idx = worst;
        }
    }
}
