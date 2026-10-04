//! Gear hash table for the rolling content-defined chunker.
//!
//! The table is derived deterministically from a fixed seed via splitmix64,
//! so any independent implementation (e.g. the Python reference in
//! `tools/reference_chunker.py`) can reproduce it bit-for-bit without
//! sharing a generated file.

/// Seed for the gear table. ASCII "CDCSRV01" — versioned so a table change
/// is an explicit algorithm change that lands in the manifest.
pub const GEAR_SEED: u64 = 0x4344_4353_5256_3031;

/// One splitmix64 step. Returns (output, next_state).
pub const fn splitmix64(state: u64) -> (u64, u64) {
    let next = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = next;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    (z ^ (z >> 31), next)
}

/// Build the 256-entry gear table at compile time.
pub const fn build_gear_table(seed: u64) -> [u64; 256] {
    let mut table = [0u64; 256];
    let mut state = seed;
    let mut i = 0;
    while i < 256 {
        let (value, next) = splitmix64(state);
        table[i] = value;
        state = next;
        i += 1;
    }
    table
}

/// The gear table used by every chunker in this process.
pub static GEAR: [u64; 256] = build_gear_table(GEAR_SEED);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_is_deterministic_and_nontrivial() {
        let again = build_gear_table(GEAR_SEED);
        assert_eq!(GEAR, again);
        // No zero entries (a zero gear word would blind the hash to a byte value).
        assert!(GEAR.iter().all(|&w| w != 0));
        // All entries distinct.
        let mut sorted = GEAR.to_vec();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), 256);
    }

    #[test]
    fn splitmix64_matches_published_vector() {
        // splitmix64(0) first output is the well-known constant 0xe220a8397b1dcdaf.
        let (out, _) = splitmix64(0);
        assert_eq!(out, 0xe220_a839_7b1d_cdaf);
    }
}
