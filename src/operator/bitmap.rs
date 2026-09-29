//! Minimal bitset over sorted positions, used as the IEJoin active bitmap.

#[derive(Debug, Clone)]
pub struct BitMap {
    words: Vec<u64>,
    len: usize,
    set_count: usize,
}

impl BitMap {
    pub fn new(len: usize) -> Self {
        let words = vec![0u64; len.div_ceil(64)];
        Self {
            words,
            len,
            set_count: 0,
        }
    }

    #[inline]
    pub fn len(&self) -> usize {
        self.len
    }
    #[inline]
    pub fn is_empty(&self) -> bool {
        self.set_count == 0
    }
    #[inline]
    pub fn set_count(&self) -> usize {
        self.set_count
    }

    #[inline]
    pub fn set(&mut self, pos: usize) {
        debug_assert!(
            pos < self.len,
            "bitmap set out of range {pos} >= {}",
            self.len
        );
        let (w, b) = (pos / 64, pos % 64);
        let mask = 1u64 << b;
        if self.words[w] & mask == 0 {
            self.words[w] |= mask;
            self.set_count += 1;
        }
    }

    #[inline]
    pub fn contains(&self, pos: usize) -> bool {
        debug_assert!(
            pos < self.len,
            "bitmap probe out of range {pos} >= {}",
            self.len
        );
        self.words[pos / 64] & (1u64 << (pos % 64)) != 0
    }

    pub fn clear(&mut self) {
        self.words.fill(0);
        self.set_count = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_idempotent_and_membership_works() {
        let mut bm = BitMap::new(130);
        assert!(bm.is_empty());
        bm.set(0);
        bm.set(63);
        bm.set(64);
        bm.set(129);
        bm.set(64); // duplicate
        assert_eq!(bm.set_count(), 4);
        for p in [0, 63, 64, 129] {
            assert!(bm.contains(p));
        }
        assert!(!bm.contains(1));
        assert!(!bm.contains(128));
        bm.clear();
        assert!(bm.is_empty());
    }
}
