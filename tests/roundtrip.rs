//! Round-trip tests: synthetic columns are encoded by the kernel and decoded
//! BOTH by the kernel and by the independent reference decoder; results are
//! compared value-by-value against the original column.

mod common;

use common::{
    alternating_column, bitwidth_crossing_column, digest, reference_decode, tail_partial_column,
    TestLog, XorShift,
};
use rib::budget::DecodeBudget;
use rib::decode::decode_column;
use rib::encode::{encode_column, EncodeOptions};

fn check_roundtrip(case: &str, values: &[u64]) {
    let log = TestLog::new(case);
    log.step(
        "input",
        &format!("values={} digest={:016x}", values.len(), digest(values)),
    );

    let bytes = encode_column(values, &EncodeOptions::default());
    log.step(
        "encode",
        &format!("bytes={} ratio={:.3}", bytes.len(), bytes.len() as f64 / (values.len().max(1) * 8) as f64),
    );

    let kernel = decode_column(&bytes, &DecodeBudget::default()).expect("kernel decode");
    log.step("decode-kernel", &format!("values={}", kernel.len()));
    assert_eq!(kernel, values, "kernel round-trip mismatch");

    let reference = reference_decode(&bytes).expect("reference decode");
    log.step("decode-reference", &format!("values={}", reference.len()));
    assert_eq!(reference, values, "reference round-trip mismatch");

    log.verdict(true, "kernel == reference == original, value-by-value");
}

#[test]
fn alternating_long_runs_and_short_variations() {
    // 40 cycles of (long run, 1..7 literals): forces repeated mode switches.
    let values = alternating_column(0x5EED_0001, 40);
    check_roundtrip("alternating", &values);
}

#[test]
fn mode_switch_boundaries_keep_exact_values() {
    // Hand-built boundary pattern: run of exactly min_run, single literal,
    // run of min_run - 1 (must stay BITPACK), run of min_run.
    let mut values = vec![42u64; 8];
    values.push(7);
    values.extend_from_slice(&[9u64; 7]);
    values.extend_from_slice(&[1000u64; 8]);
    values.push(3);
    check_roundtrip("mode-switch-boundary", &values);
}

#[test]
fn bitwidth_boundaries_crossing() {
    let values = bitwidth_crossing_column();
    check_roundtrip("bitwidth-crossing", &values);
}

#[test]
fn bitwidth_zero_all_zeros() {
    // All-zero column: bit_width 0 is legal and must round-trip.
    let values = vec![0u64; 37];
    check_roundtrip("bitwidth-zero", &values);
}

#[test]
fn bitwidth_max_full_u64() {
    // Values needing the full 64 bits, including u64::MAX.
    let mut rng = XorShift::new(0xFFFF_0001);
    let mut values: Vec<u64> = (0..50).map(|_| rng.next() | (1u64 << 63)).collect();
    values.push(u64::MAX);
    check_roundtrip("bitwidth-max", &values);
}

#[test]
fn tail_partial_group_not_counted() {
    let values = tail_partial_column();
    let log = TestLog::new("tail-partial");
    log.step("input", &format!("values={} digest={:016x}", values.len(), digest(&values)));
    let bytes = encode_column(&values, &EncodeOptions::default());
    let decoded = decode_column(&bytes, &DecodeBudget::default()).unwrap();
    // 13 >= min_run so the head is RLE; the 5 trailing literals form a
    // BITPACK block whose group is padded to 8. Decoded length must equal
    // the original exactly — padding must not appear.
    assert_eq!(decoded.len(), values.len(), "tail padding leaked into output");
    assert_eq!(decoded, values);
    log.verdict(true, "decoded length == input length; padding excluded");
}

#[test]
fn empty_column_roundtrip() {
    let bytes = encode_column(&[], &EncodeOptions::default());
    assert!(bytes.is_empty());
    let decoded = decode_column(&bytes, &DecodeBudget::default()).unwrap();
    assert!(decoded.is_empty());
}

#[test]
fn single_value_column() {
    check_roundtrip("single-value", &[123_456u64]);
}
