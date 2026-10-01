//! Fixed-universe packed bitmaps.
//!
//! Bits are packed LSB-first into `u64` words. The invariant
//! [`Bitmap::well_formed`] enforces that every bit at index `>= len` (the
//! "tail padding" of the last machine word) is zero. Every constructor and
//! operator preserves the invariant, so set-algebra never accidentally counts
//! a padding bit as a row. This is the property that naive
//! `word ^= u64::MAX` negation would violate; [`Tricolor`] in
//! [`super::tricolor`] computes NOT with the universe instead.

use crate::error::{Result, TviError};

/// Number of rows carried in one machine word.
const WORD_BITS: usize = u64::BITS as usize;

/// A read-only bit set over `0..len` rows.
#[derive(Clone, Eq, PartialEq)]
pub struct Bitmap {
    /// Packed bits, one row per bit (row `i` -> word `i/64`, bit `i%64`).
    words: Vec<u64>,
    /// Number of valid rows; bits at index >= len are always zero.
    len: usize,
}

impl std::fmt::Debug for Bitmap {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Bitmap[len={}, bits={}]", self.len, self.to_bit_string())
    }
}

impl Bitmap {
    /// The empty bitmap over a universe of `len` rows.
    pub fn zeros(len: usize) -> Self {
        let words = vec![0u64; words_for(len)];
        let bm = Bitmap { words, len };
        debug_assert!(bm.well_formed());
        bm
    }

    /// Build a bitmap from already-packed words.
    ///
    /// # Errors
    /// Returns [`TviError::InvalidQuery`] if `words` has the wrong length or
    /// any tail-padding bit is set.
    pub fn from_words(len: usize, words: Vec<u64>) -> Result<Self> {
        let need = words_for(len);
        if words.len() != need {
            return Err(TviError::InvalidQuery(format!(
                "bitmap with {len} rows needs {need} words, got {}",
                words.len()
            )));
        }
        let bm = Bitmap { words, len };
        bm.validate_tail()?;
        Ok(bm)
    }

    /// Build by setting each index in `bits` over a universe of `len` rows.
    pub fn from_indices(len: usize, bits: impl IntoIterator<Item = usize>) -> Result<Self> {
        let mut bm = Bitmap::zeros(len);
        for i in bits {
            if i >= len {
                return Err(TviError::InvalidQuery(format!(
                    "bit index {i} out of range for universe of {len}"
                )));
            }
            bm.words[i / WORD_BITS] |= 1u64 << (i % WORD_BITS);
        }
        debug_assert!(bm.well_formed());
        Ok(bm)
    }

    /// Universe size.
    #[inline]
    pub fn len(&self) -> usize {
        self.len
    }

    /// Whether the universe is empty.
    #[inline]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Test one row.
    #[inline]
    pub fn get(&self, i: usize) -> bool {
        debug_assert!(i < self.len, "row {i} out of range {}", self.len);
        (self.words[i / WORD_BITS] >> (i % WORD_BITS)) & 1 == 1
    }

    /// Set one row.
    #[inline]
    pub fn set(&mut self, i: usize) -> Result<()> {
        if i >= self.len {
            return Err(TviError::InvalidQuery(format!(
                "bit index {i} out of range for universe of {}",
                self.len
            )));
        }
        self.words[i / WORD_BITS] |= 1u64 << (i % WORD_BITS);
        Ok(())
    }

    /// Number of set bits.
    pub fn count_ones(&self) -> usize {
        self.words.iter().map(|w| w.count_ones() as usize).sum()
    }

    /// Iterate the set row indices in ascending order.
    pub fn iter_ones(&self) -> impl Iterator<Item = usize> + '_ {
        (0..self.len).filter(move |&i| self.get(i))
    }

    /// Machine words, for tests and interop.
    pub fn words(&self) -> &[u64] {
        &self.words
    }

    /// Set intersection.
    pub fn and(&self, other: &Self) -> Result<Self> {
        self.check_same_universe(other)?;
        let words = self
            .words
            .iter()
            .zip(&other.words)
            .map(|(a, b)| a & b)
            .collect();
        let bm = Bitmap {
            words,
            len: self.len,
        };
        debug_assert!(bm.well_formed());
        Ok(bm)
    }

    /// Set union.
    pub fn or(&self, other: &Self) -> Result<Self> {
        self.check_same_universe(other)?;
        let words = self
            .words
            .iter()
            .zip(&other.words)
            .map(|(a, b)| a | b)
            .collect();
        let bm = Bitmap {
            words,
            len: self.len,
        };
        debug_assert!(bm.well_formed());
        Ok(bm)
    }

    /// Complement *within the universe*: tail padding never becomes set.
    /// This is set NOT and must not be implemented as a machine-word NOT.
    pub fn complement(&self) -> Self {
        let mut words = self.words.clone();
        for w in &mut words {
            *w = !*w;
        }
        clear_tail(&mut words, self.len);
        let bm = Bitmap {
            words,
            len: self.len,
        };
        debug_assert!(bm.well_formed());
        bm
    }

    /// Rows in `self` but not `other`.
    pub fn and_not(&self, other: &Self) -> Result<Self> {
        self.check_same_universe(other)?;
        let mut words = self
            .words
            .iter()
            .zip(&other.words)
            .map(|(a, b)| a & !b)
            .collect::<Vec<_>>();
        clear_tail(&mut words, self.len);
        let bm = Bitmap {
            words,
            len: self.len,
        };
        debug_assert!(bm.well_formed());
        Ok(bm)
    }

    /// Disjoint test.
    pub fn is_disjoint(&self, other: &Self) -> bool {
        debug_assert_eq!(self.len, other.len);
        self.words.iter().zip(&other.words).all(|(a, b)| a & b == 0)
    }

    /// Subset test.
    pub fn is_subset(&self, other: &Self) -> bool {
        debug_assert_eq!(self.len, other.len);
        self.words
            .iter()
            .zip(&other.words)
            .all(|(a, b)| a & !b == 0)
    }

    /// Render as a 0/1 string, index 0 on the left. Used in test logs so a
    /// failing case is directly attributable to its input rows.
    pub fn to_bit_string(&self) -> String {
        (0..self.len)
            .map(|i| if self.get(i) { '1' } else { '0' })
            .collect()
    }

    /// Ensure two operands share one document universe.
    pub(crate) fn check_same_universe(&self, other: &Self) -> Result<()> {
        if self.len != other.len {
            return Err(TviError::UniverseMismatch {
                expected: self.len,
                found: other.len,
            });
        }
        Ok(())
    }

    /// Verify tail-padding bits are zero.
    fn validate_tail(&self) -> Result<()> {
        if self.len % WORD_BITS != 0 {
            let last = *self.words.last().unwrap_or(&0);
            let valid = self.len % WORD_BITS;
            let mask = if valid == WORD_BITS {
                u64::MAX
            } else {
                (1u64 << valid) - 1
            };
            if last & !mask != 0 {
                return Err(TviError::InvalidQuery(format!(
                    "bitmap over {}-row universe has set bits beyond the end (last word {last:#018x}, valid mask {mask:#018x})",
                    self.len
                )));
            }
        }
        Ok(())
    }

    /// Invariant: all padding bits clear and word count matches length.
    /// Called only from `debug_assert!` (compiled out in release builds).
    #[allow(dead_code)]
    fn well_formed(&self) -> bool {
        self.validate_tail().is_ok() && self.words.len() == words_for(self.len)
    }
}

#[inline]
fn words_for(len: usize) -> usize {
    len.div_ceil(WORD_BITS)
}

/// Zero the invalid bits of the final word of a length-`len` bitmap.
#[inline]
pub(crate) fn clear_tail(words: &mut [u64], len: usize) {
    let rem = len % WORD_BITS;
    if rem != 0 {
        if let Some(last) = words.last_mut() {
            let mask = (1u64 << rem) - 1;
            *last &= mask;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn complement_respects_non_word_sized_universe() {
        // 67 rows = two words; without tail clearing, complement would set
        // 61 phantom rows in the last word.
        let bm = Bitmap::from_indices(67, [0, 66]).unwrap();
        let c = bm.complement();
        assert_eq!(c.count_ones(), 65);
        assert!(!c.get(66));
        assert!(c.get(1));
    }

    #[test]
    fn rejects_dirty_tail_on_construction() {
        let len = 3usize; // one word, only bits 0..3 valid
        let err = Bitmap::from_words(len, vec![0b1111]).unwrap_err();
        assert!(matches!(err, TviError::InvalidQuery(_)));
    }

    #[test]
    fn empty_and_word_sized_universes() {
        let e = Bitmap::zeros(0);
        assert_eq!(e.complement().count_ones(), 0);
        let w = Bitmap::zeros(64);
        assert_eq!(w.complement().count_ones(), 64);
    }
}
