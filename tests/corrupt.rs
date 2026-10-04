//! Corruption tests: damaged block headers and payloads must be *located*
//! (structured error with category + byte offset), never read out of bounds
//! or silently accepted.

mod common;

use common::{digest, TestLog};
use rib::budget::DecodeBudget;
use rib::decode::decode_column;
use rib::encode::{encode_column, EncodeOptions};
use rib::error::ErrorCategory;

fn sample_column() -> Vec<u64> {
    let mut v = vec![77u64; 20];
    v.extend_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8, 9]);
    v
}

fn expect_error(case: &str, bytes: &[u8], category: ErrorCategory, offset: usize) {
    let log = TestLog::new(case);
    log.step("input", &format!("bytes={} digest={:016x}", bytes.len(), {
        let vals: Vec<u64> = bytes.iter().map(|&b| b as u64).collect();
        digest(&vals)
    }));
    match decode_column(bytes, &DecodeBudget::default()) {
        Ok(values) => {
            log.verdict(false, "corrupted input decoded successfully");
            panic!("expected {:?} at {}, got {} values", category, offset, values.len());
        }
        Err(e) => {
            log.step(
                "located",
                &format!("category={} offset={} detail={}", e.category.as_str(), e.offset, e.detail),
            );
            assert_eq!(e.category, category, "wrong error category");
            assert_eq!(e.offset, offset, "wrong error offset");
            log.verdict(true, "error category and offset match expectation");
        }
    }
}

#[test]
fn truncated_header_is_located() {
    let bytes = encode_column(&sample_column(), &EncodeOptions::default());
    expect_error("truncated-header", &bytes[..10], ErrorCategory::TruncatedHeader, 10);
}

#[test]
fn bad_magic_is_located_at_zero() {
    let mut bytes = encode_column(&sample_column(), &EncodeOptions::default());
    bytes[1] = b'X';
    expect_error("bad-magic", &bytes, ErrorCategory::BadMagic, 0);
}

#[test]
fn bad_version_is_located() {
    let mut bytes = encode_column(&sample_column(), &EncodeOptions::default());
    bytes[4] = 99;
    expect_error("bad-version", &bytes, ErrorCategory::UnsupportedVersion, 4);
}

#[test]
fn unknown_mode_is_located() {
    let mut bytes = encode_column(&sample_column(), &EncodeOptions::default());
    bytes[5] = 7;
    expect_error("unknown-mode", &bytes, ErrorCategory::UnknownMode, 5);
}

#[test]
fn bitwidth_65_is_illegal() {
    let mut bytes = encode_column(&sample_column(), &EncodeOptions::default());
    bytes[6] = 65;
    expect_error("bitwidth-65", &bytes, ErrorCategory::InvalidBitWidth, 6);
}

#[test]
fn nonzero_reserved_is_located() {
    let mut bytes = encode_column(&sample_column(), &EncodeOptions::default());
    bytes[7] = 1;
    expect_error("reserved", &bytes, ErrorCategory::NonZeroReserved, 7);
}

#[test]
fn bitpack_payload_len_mismatch_is_located() {
    let mut bytes = encode_column(&[1u64, 2, 3], &EncodeOptions::default());
    // 3 values at width 2 -> one group -> 2 bytes; claim 5.
    bytes[12..16].copy_from_slice(&5u32.to_le_bytes());
    expect_error(
        "bitpack-len-mismatch",
        &bytes,
        ErrorCategory::PayloadLenMismatch,
        12,
    );
}

#[test]
fn truncated_payload_is_located_not_overrun() {
    let bytes = encode_column(&sample_column(), &EncodeOptions::default());
    let cut = bytes.len() - 2;
    let err = decode_column(&bytes[..cut], &DecodeBudget::default()).unwrap_err();
    assert_eq!(err.category, ErrorCategory::TruncatedPayload);
    assert!(err.offset <= cut, "error offset must stay inside the buffer");
}

#[test]
fn declared_payload_beyond_buffer_is_located() {
    let mut bytes = encode_column(&[5u64; 10], &EncodeOptions::default());
    // Inflate payload_len far beyond the actual buffer.
    bytes[12..16].copy_from_slice(&10_000u32.to_le_bytes());
    let err = decode_column(&bytes, &DecodeBudget::default()).unwrap_err();
    assert_eq!(err.category, ErrorCategory::TruncatedPayload);
}

#[test]
fn value_count_over_budget_is_rejected_before_decode() {
    let mut bytes = encode_column(&[1u64; 10], &EncodeOptions::default());
    bytes[8..12].copy_from_slice(&2_000_000u32.to_le_bytes());
    let err = decode_column(&bytes, &DecodeBudget::default()).unwrap_err();
    assert_eq!(err.category, ErrorCategory::ValueCountOverBudget);
    assert_eq!(err.offset, 8);
}

#[test]
fn payload_len_over_budget_is_rejected_before_decode() {
    let mut bytes = encode_column(&[1u64; 10], &EncodeOptions::default());
    // RLE block: payload_len check happens before the bitpack exactness check.
    bytes[12..16].copy_from_slice(&(u32::MAX).to_le_bytes());
    let err = decode_column(&bytes, &DecodeBudget::default()).unwrap_err();
    assert_eq!(err.category, ErrorCategory::PayloadLenOverBudget);
    assert_eq!(err.offset, 12);
}

#[test]
fn rle_zero_run_length_is_located() {
    let mut bytes = encode_column(&[9u64; 10], &EncodeOptions::default());
    assert_eq!(bytes[5], 1, "expected an RLE block");
    bytes[16..20].copy_from_slice(&0u32.to_le_bytes()); // run_len = 0
    expect_error("rle-zero-run", &bytes, ErrorCategory::RunLengthZero, 16);
}

#[test]
fn rle_run_sum_shortfall_is_located() {
    let mut bytes = encode_column(&[9u64; 10], &EncodeOptions::default());
    assert_eq!(bytes[5], 1, "expected an RLE block");
    bytes[16..20].copy_from_slice(&7u32.to_le_bytes()); // runs sum to 7, count says 10
    // The payload ends before value_count values are produced: reported as a
    // truncated payload at the exact end-of-payload offset.
    expect_error(
        "rle-sum-shortfall",
        &bytes,
        ErrorCategory::TruncatedPayload,
        21,
    );
}

#[test]
fn rle_run_sum_overflow_is_located() {
    let mut bytes = encode_column(&[9u64; 10], &EncodeOptions::default());
    assert_eq!(bytes[5], 1, "expected an RLE block");
    bytes[16..20].copy_from_slice(&11u32.to_le_bytes()); // runs sum to 11 > count 10
    expect_error(
        "rle-sum-overflow",
        &bytes,
        ErrorCategory::RunLengthSumMismatch,
        16, // detected at the offending run
    );
}
