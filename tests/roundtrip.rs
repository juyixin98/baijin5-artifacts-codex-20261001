//! Roundtrip tests: encode -> decode must reproduce the source column
//! value-for-value. Covers alternating long repeats / short variations,
//! bit-width boundary crossing, and non-integral tail groups.

mod common;

use common::{input_fingerprint, TestLog};
use rbp_column::decode::{decode_column, DecodeBudget};
use rbp_column::encode::{encode_column, EncoderConfig};

/// Deterministic xorshift so the "random" cases are reproducible
/// without an external RNG dependency.
struct XorShift(u64);

impl XorShift {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    /// A value constrained to `width` bits (width 0..=64).
    fn next_bounded(&mut self, width: u32) -> u64 {
        debug_assert!(width <= 64);
        let mask = if width == 64 { u64::MAX } else { (1u64 << width) - 1 };
        self.next() & mask
    }
}

fn roundtrip(case: &str, values: &[u64]) {
    let mut log = TestLog::new(case);
    log.step(&format!(
        "input_values={} fingerprint={}",
        values.len(),
        input_fingerprint(values)
    ));

    let bytes = encode_column(values, &EncoderConfig::default())
        .unwrap_or_else(|e| panic!("{case}: encode failed: {e}"));
    log.step(&format!("encoded_bytes={}", bytes.len()));

    let (decoded, stats) = decode_column(&bytes, &DecodeBudget::default())
        .unwrap_or_else(|e| panic!("{case}: decode failed: {e}"));
    log.step(&format!(
        "decoded_values={} blocks={} (rle={} bitpack={})",
        decoded.len(), stats.blocks, stats.rle_blocks, stats.bitpack_blocks
    ));

    assert_eq!(decoded.len(), values.len(), "{case}: length mismatch");
    for (i, (got, want)) in decoded.iter().zip(values.iter()).enumerate() {
        assert_eq!(got, want, "{case}: value {i} mismatch");
    }
    log.pass("per-value equality with source column after roundtrip");
}

#[test]
fn alternating_long_repeats_and_short_variations() {
    let mut values = Vec::new();
    for k in 0..20u64 {
        values.extend(std::iter::repeat_n(k * 7, 8 + (k as usize % 5) * 3));
        let lit_len = 1 + (k as usize % 7);
        for j in 0..lit_len {
            values.push(k * 100 + j as u64);
        }
    }
    roundtrip("alternating-runs-x20", &values);
}

#[test]
fn bitwidth_boundary_crossing() {
    let mut values = vec![
        0,
        1,
        (1 << 8) - 1,
        1 << 8,
        (1 << 16) - 1,
        1 << 16,
        (1 << 32) - 1,
        1 << 32,
        (1 << 63) - 1,
        1 << 63,
        u64::MAX,
    ];
    // Same boundaries again as long runs (RLE at each width).
    for shift in [0, 8, 16, 32, 48, 63] {
        let v = (1u64 << shift).wrapping_sub(1);
        values.extend(std::iter::repeat_n(v, 9));
    }
    values.extend(std::iter::repeat_n(u64::MAX, 8));
    roundtrip("bitwidth-crossing", &values);
}

#[test]
fn non_integral_tail_groups() {
    for count in 1..=17usize {
        let values: Vec<u64> = (0..count as u64).map(|v| v * v + 1).collect();
        roundtrip(&format!("tail-group-len-{count}"), &values);
    }
}

#[test]
fn zero_and_max_bitwidth_columns() {
    roundtrip("all-zeros-64", &vec![0u64; 64]);
    roundtrip("all-max-64", &vec![u64::MAX; 64]);
    roundtrip("single-zero", &[0]);
    roundtrip("single-max", &[u64::MAX]);
}

#[test]
fn mode_switch_preserves_boundary_values() {
    // A value equal to an adjacent run's value must land in exactly one block.
    let mut values = vec![5, 5, 5]; // 3 literals
    values.extend(std::iter::repeat_n(5, 0)); // (segmentation merges nothing here)
    values.extend(std::iter::repeat_n(9, 8)); // RLE run
    values.push(9); // trailing literal equal to the RLE value
    values.extend(std::iter::repeat_n(5, 8)); // RLE run of the literal value
    values.push(5);
    roundtrip("mode-switch-boundaries", &values);
}

#[test]
fn deterministic_mixed_stress() {
    let mut rng = XorShift(0x1234_5678_9abc_def0);
    let mut values = Vec::new();
    for _ in 0..300 {
        if rng.next().is_multiple_of(2) {
            let width = (rng.next() % 65) as u32;
            let v = rng.next_bounded(width);
            let n = 8 + (rng.next() % 40) as usize;
            values.extend(std::iter::repeat_n(v, n));
        } else {
            let n = 1 + (rng.next() % 15) as usize;
            for _ in 0..n {
                let width = (1 + rng.next() % 64) as u32;
                values.push(rng.next_bounded(width));
            }
        }
    }
    roundtrip("deterministic-mixed-stress-300-segments", &values);
}
