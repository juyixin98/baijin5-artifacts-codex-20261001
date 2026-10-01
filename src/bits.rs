//! Word-packed bitmaps over a fixed universe length.
//!
//! Safety properties enforced here, relied on by the whole crate:
//! * invalid (padding) bits in the trailing byte are ALWAYS zero, so a naive
//!   `!word` can never resurrect rows outside the universe;
//! * bit indices are bounds-checked against `len`, never against byte capacity;
//! * all binary operations require equal length (same document universe).

use crate::error::{Error, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bitmap {
    /// One bit per document position, LSB-first within each byte.
    bytes: Vec<u8>,
    /// Number of VALID bits; bits at positions >= len are padding and stay 0.
    len: usize,
}

impl Bitmap {
    /// All-false bitmap of `len` valid positions.
    pub fn zeros(len: usize) -> Self {
        Self {
            bytes: vec![0u8; bytes_for(len)],
            len,
        }
    }

    /// All-true bitmap of `len` valid positions; tail padding cleared.
    pub fn ones(len: usize) -> Self {
        if len == 0 {
            return Self {
                bytes: Vec::new(),
                len: 0,
            };
        }
        let mut bytes = vec![0xFFu8; bytes_for(len)];
        clear_tail(&mut bytes, len);
        Self { bytes, len }
    }

    /// Build from raw bytes; trailing invalid bits are forcibly cleared and the
    /// buffer must be exactly `ceil(len/8)` long.
    pub fn from_bytes(len: usize, raw: Vec<u8>) -> Result<Self> {
        if raw.len() != bytes_for(len) {
            return Err(Error::new(
                crate::error::ErrorKind::BitmapShape,
                format!(
                    "expected {} bytes for {} bits, got {}",
                    bytes_for(len),
                    len,
                    raw.len()
                ),
            ));
        }
        let mut bm = Self { bytes: raw, len };
        clear_tail(&mut bm.bytes, len);
        Ok(bm)
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    #[inline]
    pub fn get(&self, idx: usize) -> bool {
        assert!(idx < self.len, "bit index {idx} out of range {}", self.len);
        (self.bytes[idx >> 3] >> (idx & 7)) & 1 == 1
    }

    #[inline]
    pub fn set(&mut self, idx: usize, value: bool) {
        assert!(idx < self.len, "bit index {idx} out of range {}", self.len);
        let mask = 1u8 << (idx & 7);
        if value {
            self.bytes[idx >> 3] |= mask;
        } else {
            self.bytes[idx >> 3] &= !mask;
        }
    }

    /// Count of set bits (only valid positions counted; tail is zero anyway).
    pub fn count_ones(&self) -> usize {
        self.bytes.iter().map(|b| b.count_ones() as usize).sum()
    }

    pub fn iter(&self) -> impl Iterator<Item = bool> + '_ {
        (0..self.len).map(|i| self.get(i))
    }

    /// Indices of every set bit.
    pub fn set_indices(&self) -> Vec<usize> {
        let mut out = Vec::new();
        for (byte_idx, &b) in self.bytes.iter().enumerate() {
            let base = byte_idx * 8;
            for bit in 0..8u32 {
                let idx = base + bit as usize;
                if idx < self.len && b & (1 << bit) != 0 {
                    out.push(idx);
                }
            }
        }
        out
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }

    fn check_same_universe(&self, other: &Self) -> Result<()> {
        if self.len != other.len {
            return Err(Error::new(
                crate::error::ErrorKind::UniverseMismatch,
                format!("bitmap lengths differ: {} vs {}", self.len, other.len),
            ));
        }
        Ok(())
    }

    pub fn and(&self, other: &Self) -> Result<Self> {
        self.check_same_universe(other)?;
        let bytes = self
            .bytes
            .iter()
            .zip(&other.bytes)
            .map(|(a, b)| a & b)
            .collect();
        Ok(Self {
            bytes,
            len: self.len,
        })
    }

    pub fn or(&self, other: &Self) -> Result<Self> {
        self.check_same_universe(other)?;
        let bytes = self
            .bytes
            .iter()
            .zip(&other.bytes)
            .map(|(a, b)| a | b)
            .collect();
        Ok(Self {
            bytes,
            len: self.len,
        })
    }

    pub fn xor(&self, other: &Self) -> Result<Self> {
        self.check_same_universe(other)?;
        let bytes = self
            .bytes
            .iter()
            .zip(&other.bytes)
            .map(|(a, b)| a ^ b)
            .collect();
        Ok(Self {
            bytes,
            len: self.len,
        })
    }

    /// Set difference: bits in `self` that are not in `other`.
    pub fn and_not(&self, other: &Self) -> Result<Self> {
        self.check_same_universe(other)?;
        let bytes = self
            .bytes
            .iter()
            .zip(&other.bytes)
            .map(|(a, b)| a & !b)
            .collect();
        Ok(Self {
            bytes,
            len: self.len,
        })
    }

    pub fn is_disjoint(&self, other: &Self) -> bool {
        self.bytes.iter().zip(&other.bytes).all(|(a, b)| a & b == 0)
    }
}

#[inline]
pub fn bytes_for(len: usize) -> usize {
    len.div_ceil(8)
}

/// Zero the padding bits of the final byte so they can never be mistaken for rows.
fn clear_tail(bytes: &mut [u8], len: usize) {
    if len == 0 {
        return;
    }
    let rem = len & 7;
    if rem != 0 {
        // Keep only the low `rem` bits of the last byte.
        let mask = (1u8 << rem) - 1;
        let last = bytes.len() - 1;
        bytes[last] &= mask;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ones_has_cleared_tail_for_non_word_length() {
        // 13 valid bits: top 3 bits of byte 1 must be 0.
        let bm = Bitmap::ones(13);
        assert_eq!(bm.as_bytes(), &[0xFF, 0b0001_1111]);
        assert_eq!(bm.count_ones(), 13);
    }

    #[test]
    fn from_bytes_clears_dirty_tail() {
        let bm = Bitmap::from_bytes(5, vec![0xFF]).unwrap();
        assert_eq!(bm.as_bytes(), &[0b0001_1111]);
        assert_eq!(bm.count_ones(), 5);
    }

    #[test]
    fn from_bytes_rejects_wrong_shape() {
        let err = Bitmap::from_bytes(9, vec![0]).unwrap_err();
        assert_eq!(err.kind, crate::error::ErrorKind::BitmapShape);
    }

    #[test]
    fn set_cannot_set_padding() {
        let mut bm = Bitmap::zeros(10);
        bm.set(9, true);
        assert_eq!(bm.as_bytes(), &[0, 0b0000_0010]);
        assert_eq!(bm.count_ones(), 1);
    }

    #[test]
    fn ops_reject_universe_mismatch() {
        let a = Bitmap::zeros(8);
        let b = Bitmap::zeros(9);
        assert_eq!(
            a.and(&b).unwrap_err().kind,
            crate::error::ErrorKind::UniverseMismatch
        );
        assert_eq!(
            a.and_not(&b).unwrap_err().kind,
            crate::error::ErrorKind::UniverseMismatch
        );
    }

    #[test]
    fn exact_multiple_of_eight_has_no_tail_masking() {
        let bm = Bitmap::ones(16);
        assert_eq!(bm.as_bytes(), &[0xFF, 0xFF]);
        assert_eq!(bm.count_ones(), 16);
    }

    #[test]
    fn empty_universe_is_shape_zero() {
        let bm = Bitmap::ones(0);
        assert!(bm.is_empty());
        assert!(bm.as_bytes().is_empty());
    }
}
