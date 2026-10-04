//! Shared helpers for integration tests: an independent, non-rolling
//! reference implementation of the chunking algorithm.
//!
//! Unlike `chunker::Chunker` (which carries rolling state across bytes), this
//! reference recomputes the window hash from scratch at every position. It
//! shares only the published spec constants (seed, window, boundary rule) —
//! not the implementation under test.

// Each integration-test binary compiles this module separately and may not
// use every helper.
#![allow(dead_code)]

pub const TABLE_SEED: u64 = 0x9E37_79B9_7F4A_7C15;
pub const GOLDEN_GAMMA: u64 = 0x9E37_79B9_7F4A_7C15;
pub const WINDOW: usize = 64;

pub fn gear_table() -> [u64; 256] {
    let mut state = TABLE_SEED;
    let mut table = [0u64; 256];
    for slot in table.iter_mut() {
        state = state.wrapping_add(GOLDEN_GAMMA);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^= z >> 31;
        *slot = z;
    }
    table
}

/// Reference boundaries: chunk-start offsets (always starts with 0 for
/// non-empty input). Recomputes the hash over the trailing window at each
/// position instead of rolling.
pub fn reference_boundaries(data: &[u8], min_size: usize, avg_bits: u32, max_size: usize) -> Vec<u64> {
    let gear = gear_table();
    let mask = (1u64 << avg_bits) - 1;
    let mut out = Vec::new();
    if data.is_empty() {
        return out;
    }
    out.push(0u64);
    let mut start = 0usize;
    for i in 0..data.len() {
        // Recompute the hash over data[max(start, i+1-WINDOW)..=i].
        // Bytes older than 64 positions contribute nothing (1-bit shift).
        let lo = start.max(i + 1 - WINDOW.min(i + 1));
        let mut h = 0u64;
        for &b in &data[lo..=i] {
            h = (h << 1).wrapping_add(gear[b as usize]);
        }
        let len = i + 1 - start;
        if len >= max_size || (len >= min_size && (h & mask) == 0) {
            if i + 1 < data.len() {
                out.push((i + 1) as u64);
            }
            start = i + 1;
        }
    }
    out
}

/// Deterministic byte source for tests (SplitMix64).
pub struct SplitMix(pub u64);

impl SplitMix {
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(GOLDEN_GAMMA);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    pub fn bytes(&mut self, n: usize) -> Vec<u8> {
        let mut out = Vec::with_capacity(n);
        while out.len() < n {
            out.extend_from_slice(&self.next_u64().to_le_bytes());
        }
        out.truncate(n);
        out
    }
}
