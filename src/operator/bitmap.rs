//! Dense candidate bitmap plus instrumentation counters.
//!
//! IEJoin maintains one bitmap per predicate over the *left* relation in
//! the order of that predicate's permutation. A bit means "the left row
//! at this permutation position currently satisfies the predicate for
//! the right row being processed". Bits are only ever set monotonically
//! during one pass and cleared between right rows.

/// Iterator over the set bits of one 64-bit word, in ascending order.
struct OnesInWord {
    word: u64,
    base: usize,
}

impl Iterator for OnesInWord {
    type Item = usize;
    fn next(&mut self) -> Option<usize> {
        if self.word == 0 {
            return None;
        }
        let tz = self.word.trailing_zeros() as usize;
        self.word &= self.word - 1;
        Some(self.base + tz)
    }
}

/// Fixed-size dense bitset over `usize` positions.
#[derive(Clone, Debug)]
pub struct BitMap {
    words: Vec<u64>,
    len: usize,
}

impl BitMap {
    #[must_use]
    pub fn new(len: usize) -> Self {
        let words = len.div_ceil(64);
        Self {
            words: vec![0; words],
            len,
        }
    }

    #[inline]
    pub fn set(&mut self, pos: usize) {
        debug_assert!(
            pos < self.len,
            "bitmap position {pos} out of range {len}",
            len = self.len
        );
        self.words[pos >> 6] |= 1u64 << (pos & 63);
    }

    #[inline]
    #[must_use]
    pub fn get(&self, pos: usize) -> bool {
        if pos >= self.len {
            return false;
        }
        (self.words[pos >> 6] >> (pos & 63)) & 1 == 1
    }

    pub fn clear(&mut self) {
        self.words.fill(0);
    }

    /// Borrow the backing words (for checkpoints).
    #[must_use]
    pub fn words(&self) -> &[u64] {
        &self.words
    }

    /// Rebuild from checkpointed words.
    ///
    /// # Panics
    /// On a word/len mismatch (only reachable with a corrupt checkpoint;
    /// engine-level deserialization validates length first).
    #[must_use]
    pub fn from_words(words: Vec<u64>, len: usize) -> Self {
        assert_eq!(
            words.len(),
            len.div_ceil(64),
            "checkpoint bitmap shape mismatch"
        );
        Self { words, len }
    }

    #[must_use]
    pub fn count_ones(&self) -> usize {
        self.words.iter().map(|w| w.count_ones() as usize).sum()
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.len
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.count_ones() == 0
    }

    /// Iterate set bit positions in ascending order.
    pub fn iter_ones(&self) -> impl Iterator<Item = usize> + '_ {
        self.words
            .iter()
            .enumerate()
            .flat_map(|(wi, &word)| OnesInWord {
                word,
                base: wi * 64,
            })
    }

    /// Smallest set position `>= from`, or `None`.
    #[must_use]
    pub fn next_one(&self, from: usize) -> Option<usize> {
        if from >= self.len {
            return None;
        }
        let mut wi = from >> 6;
        let bit = from & 63;
        let mut w = self.words[wi] & (!0u64 << bit);
        loop {
            if w != 0 {
                let pos = wi * 64 + w.trailing_zeros() as usize;
                return if pos < self.len { Some(pos) } else { None };
            }
            wi += 1;
            if wi >= self.words.len() {
                return None;
            }
            w = self.words[wi];
        }
    }
}

/// Instrumentation: how much work did an operator perform?
///
/// `candidate_accesses` is the key metric required by the tests: it
/// counts every left-row position the join actually inspected while
/// producing output, so test cases can prove the IEJoin plan visits far
/// fewer candidates than a nested loop on selective data.
#[derive(Clone, Debug, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Counters {
    /// Right rows examined (non-NULL in both key columns).
    pub right_rows_scanned: u64,
    /// Predicate-1 monotone gate element advances over permutation 1.
    pub gate1_steps: u64,
    /// Key comparisons used by the predicate-2 boundary binary searches
    /// (one search per opened right row).
    pub gate2_steps: u64,
    /// Left candidate positions inspected while emitting pairs.
    pub candidate_accesses: u64,
    /// Output pairs emitted (before any budget truncation).
    pub pairs_emitted: u64,
}

impl Counters {
    pub fn add(&mut self, other: &Counters) {
        self.right_rows_scanned += other.right_rows_scanned;
        self.candidate_accesses += other.candidate_accesses;
        self.gate1_steps += other.gate1_steps;
        self.gate2_steps += other.gate2_steps;
        self.pairs_emitted += other.pairs_emitted;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bitmap_set_iter_clear() {
        let mut b = BitMap::new(130);
        assert!(b.is_empty());
        b.set(0);
        b.set(65);
        b.set(129);
        assert_eq!(b.count_ones(), 3);
        let v: Vec<_> = b.iter_ones().collect();
        assert_eq!(v, vec![0, 65, 129]);
        assert!(b.get(65));
        assert!(!b.get(64));
        b.clear();
        assert_eq!(b.count_ones(), 0);
    }
}
