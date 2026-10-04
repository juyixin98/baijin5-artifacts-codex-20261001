//! Gear rolling hash (LBFS/restic family) used for content-defined chunking.
//!
//! Specification (must stay stable for interop):
//! - `GEAR[i]` for `i` in `0..256` is the i-th output of a SplitMix64 generator
//!   whose state starts at `TABLE_SEED` and advances by the golden-ratio
//!   increment `0x9E3779B97F4A7C15` per output.
//! - Rolling update per byte `b`: `h = (h << 1).wrapping_add(GEAR[b as usize])`.
//! - Because the state is 64 bits and each step shifts left by 1, a byte only
//!   influences the hash for the next 64 bytes: the effective window is
//!   `WINDOW_BYTES = 64`.
//! - The hash state is reset to 0 at every chunk boundary, so a boundary
//!   decision depends only on the bytes of the current chunk.

/// Seed of the SplitMix64 sequence that produces the gear table.
pub const TABLE_SEED: u64 = 0x9E37_79B9_7F4A_7C15;

/// Effective rolling window in bytes (64-bit state, 1-bit shift per byte).
pub const WINDOW_BYTES: usize = 64;

const GOLDEN_GAMMA: u64 = 0x9E37_79B9_7F4A_7C15;

const fn splitmix64_next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(GOLDEN_GAMMA);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

const fn build_table() -> [u64; 256] {
    let mut state: u64 = TABLE_SEED;
    let mut table = [0u64; 256];
    let mut i = 0;
    while i < 256 {
        table[i] = splitmix64_next(&mut state);
        i += 1;
    }
    table
}

/// Fixed gear table, fully determined by [`TABLE_SEED`].
pub const GEAR_TABLE: [u64; 256] = build_table();

/// Rolling gear hash state. Cheap to reset; not Clone on purpose so that
/// chunkers own their state explicitly.
pub struct GearHash {
    state: u64,
}

impl GearHash {
    pub fn new() -> Self {
        GearHash { state: 0 }
    }

    pub fn reset(&mut self) {
        self.state = 0;
    }

    /// Feed one byte and return the new state.
    #[inline]
    pub fn roll(&mut self, byte: u8) -> u64 {
        self.state = (self.state << 1).wrapping_add(GEAR_TABLE[byte as usize]);
        self.state
    }

    pub fn state(&self) -> u64 {
        self.state
    }
}

impl Default for GearHash {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_is_deterministic_and_distinct() {
        let mut seen = std::collections::HashSet::new();
        for &v in GEAR_TABLE.iter() {
            seen.insert(v);
        }
        // A sane random table has (almost) no collisions.
        assert!(seen.len() >= 250, "gear table has too many collisions");
        // Recompute independently and compare.
        let mut state = TABLE_SEED;
        for &v in GEAR_TABLE.iter() {
            state = state.wrapping_add(GOLDEN_GAMMA);
            let mut z = state;
            z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            z ^= z >> 31;
            assert_eq!(v, z);
        }
    }

    #[test]
    fn window_is_64_bytes() {
        // Two hashes fed with sequences that differ only more than 64 bytes
        // back must agree.
        let mut a = GearHash::new();
        let mut b = GearHash::new();
        a.roll(0xAA);
        b.roll(0x55);
        for i in 0..WINDOW_BYTES {
            let byte = (i as u8).wrapping_mul(31);
            a.roll(byte);
            b.roll(byte);
        }
        assert_eq!(a.state(), b.state());
    }

    #[test]
    fn reset_clears_state() {
        let mut h = GearHash::new();
        h.roll(1);
        h.roll(2);
        h.reset();
        assert_eq!(h.state(), 0);
    }
}
