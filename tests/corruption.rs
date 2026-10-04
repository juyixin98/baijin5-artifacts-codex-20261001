//! Corruption tests: a damaged block header must be *located* (exact error
//! category + block index + byte offset), and the decoder must never read
//! past the input or panic. Assertions check concrete error categories,
//! not merely "some error happened".

mod common;

use common::TestLog;
use rbp_column::decode::{decode_column, DecodeBudget};
use rbp_column::encode::{encode_column, EncoderConfig};
use rbp_column::error::{CodecError, ErrorCategory};
use rbp_column::format::{BlockHeader, Mode, MAGIC};

/// A valid baseline stream: RLE block (7 x 8) + bitpack block (3 literals).
fn baseline_stream() -> Vec<u8> {
    let mut values = vec![7u64; 8];
    values.extend([1, 2, 3]);
    encode_column(&values, &EncoderConfig::default()).expect("baseline encodes")
}

fn expect_err(case: &str, bytes: &[u8]) -> CodecError {
    let mut log = TestLog::new(case);
    log.step(&format!("input_bytes={} hex={}", bytes.len(), hex::encode(bytes)));
    match decode_column(bytes, &DecodeBudget::default()) {
        Ok((values, _)) => panic!("{case}: corrupted input decoded successfully ({values:?})"),
        Err(e) => {
            log.step(&format!(
                "error category={:?} block_index={:?} offset={:?} msg=\"{}\"",
                e.category, e.block_index, e.offset, e.message
            ));
            e
        }
    }
}

#[test]
fn bad_magic_is_located() {
    let mut bytes = baseline_stream();
    bytes[0] = b'X';
    let e = expect_err("bad-magic", &bytes);
    assert_eq!(e.category, ErrorCategory::BadMagic);
    assert_eq!(e.offset, Some(0));
    TestLog::new("bad-magic").pass("category=bad_magic at offset 0");
}

#[test]
fn truncated_header_is_located() {
    let bytes = baseline_stream();
    // Keep magic + first block + 3 bytes of the second block's header.
    let cut = 4 + 8 + 1 + 3; // magic + header + rle body (bw(7)=3 -> 1 byte) + 3
    let truncated = &bytes[..cut];
    let e = expect_err("truncated-header", truncated);
    assert_eq!(e.category, ErrorCategory::TruncatedHeader);
    assert_eq!(e.block_index, Some(1));
    assert_eq!(e.offset, Some(cut - 3));
    TestLog::new("truncated-header").pass("category=truncated_header at block 1");
}

#[test]
fn invalid_mode_tag_is_located() {
    let mut bytes = baseline_stream();
    let second_header = 4 + 8 + 1; // offset of block 1 header
    bytes[second_header] = 7; // illegal mode tag
    let e = expect_err("invalid-mode", &bytes);
    assert_eq!(e.category, ErrorCategory::InvalidMode);
    assert_eq!(e.block_index, Some(1));
    assert_eq!(e.offset, Some(second_header));
    TestLog::new("invalid-mode").pass("category=invalid_mode at block 1");
}

#[test]
fn invalid_bit_width_is_located() {
    let mut bytes = baseline_stream();
    let second_header = 4 + 8 + 1;
    bytes[second_header + 1] = 65; // bit_width above the legal max of 64
    let e = expect_err("invalid-bit-width", &bytes);
    assert_eq!(e.category, ErrorCategory::InvalidBitWidth);
    assert_eq!(e.block_index, Some(1));
    assert_eq!(e.offset, Some(second_header));
    TestLog::new("invalid-bit-width").pass("category=invalid_bit_width at block 1");
}

#[test]
fn nonzero_reserved_field_is_located() {
    let mut bytes = baseline_stream();
    let second_header = 4 + 8 + 1;
    bytes[second_header + 2] = 0xAA; // reserved must be zero
    let e = expect_err("nonzero-reserved", &bytes);
    assert_eq!(e.category, ErrorCategory::InvalidReserved);
    assert_eq!(e.block_index, Some(1));
    TestLog::new("nonzero-reserved").pass("category=invalid_reserved at block 1");
}

#[test]
fn truncated_body_is_located_before_decode() {
    // Hand-build: magic + bitpack header declaring 8 values at bw=8 (8 body
    // bytes) but provide only 3 body bytes.
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    let header = BlockHeader {
        mode: Mode::BitPack,
        bit_width: 8,
        value_count: 8,
    };
    bytes.extend_from_slice(&header.to_bytes());
    bytes.extend_from_slice(&[1, 2, 3]);
    let e = expect_err("truncated-body", &bytes);
    assert_eq!(e.category, ErrorCategory::TruncatedBody);
    assert_eq!(e.block_index, Some(0));
    assert_eq!(e.offset, Some(4 + 8)); // body start
    assert!(e.message.contains("need") || e.message.contains("needs"));
    TestLog::new("truncated-body").pass("category=truncated_body, need 8 have 3");
}

#[test]
fn inflated_value_count_hits_budget_not_memory() {
    // RLE header declaring u32::MAX repetitions: the decoder must reject via
    // the value budget before allocating anything.
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    let header = BlockHeader {
        mode: Mode::Rle,
        bit_width: 8,
        value_count: u32::MAX,
    };
    bytes.extend_from_slice(&header.to_bytes());
    bytes.push(42);
    let budget = DecodeBudget {
        max_values: 1_000_000,
        ..DecodeBudget::default()
    };
    let mut log = TestLog::new("inflated-value-count");
    let err = match decode_column(&bytes, &budget) {
        Ok(_) => panic!("inflated count decoded successfully"),
        Err(e) => e,
    };
    log.step(&format!(
        "error category={:?} block_index={:?} msg=\"{}\"",
        err.category, err.block_index, err.message
    ));
    assert_eq!(err.category, ErrorCategory::BudgetExceeded);
    assert_eq!(err.block_index, Some(0));
    log.pass("category=budget_exceeded before any allocation");
}

#[test]
fn block_count_budget_is_enforced() {
    // 4 RLE blocks, budget allows only 2.
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    for i in 0..4u64 {
        let header = BlockHeader {
            mode: Mode::Rle,
            bit_width: 8,
            value_count: 8,
        };
        bytes.extend_from_slice(&header.to_bytes());
        bytes.push(i as u8);
    }
    let budget = DecodeBudget {
        max_blocks: 2,
        ..DecodeBudget::default()
    };
    let err = decode_column(&bytes, &budget).expect_err("block budget must fire");
    assert_eq!(err.category, ErrorCategory::BudgetExceeded);
    assert_eq!(err.block_index, Some(2));
    TestLog::new("block-count-budget")
        .pass("category=budget_exceeded at block 2 (limit 2 blocks)");
}

#[test]
fn stream_byte_budget_is_enforced() {
    let bytes = baseline_stream();
    let budget = DecodeBudget {
        max_bytes: bytes.len() - 1,
        ..DecodeBudget::default()
    };
    let err = decode_column(&bytes, &budget).expect_err("byte budget must fire");
    assert_eq!(err.category, ErrorCategory::BudgetExceeded);
    TestLog::new("stream-byte-budget").pass("category=budget_exceeded on stream size");
}

#[test]
fn empty_and_tiny_inputs_fail_cleanly() {
    let e = expect_err("empty-input", &[]);
    assert_eq!(e.category, ErrorCategory::TruncatedHeader);

    let e = expect_err("magic-only", &MAGIC);
    assert_eq!(e.category, ErrorCategory::EmptyInput);
    TestLog::new("tiny-inputs").pass("empty -> truncated_header; magic-only -> empty_input");
}

#[test]
fn fuzz_corruption_never_panics() {
    // Flip every byte of the baseline stream through several values; every
    // outcome must be Ok or a located Err — never a panic or OOB read.
    let baseline = baseline_stream();
    let mut log = TestLog::new("fuzz-byte-flips");
    let mut ok = 0usize;
    let mut located = 0usize;
    for pos in 0..baseline.len() {
        for delta in [0x01u8, 0x80, 0xFF] {
            let mut mutated = baseline.clone();
            mutated[pos] = mutated[pos].wrapping_add(delta);
            match decode_column(&mutated, &DecodeBudget::default()) {
                Ok(_) => ok += 1,
                Err(e) => {
                    assert!(
                        e.block_index.is_some() || e.category == ErrorCategory::BadMagic,
                        "error at byte {pos} lacks location: {e:?}"
                    );
                    located += 1;
                }
            }
        }
    }
    log.step(&format!(
        "mutations={} decoded_ok={} located_errors={}",
        baseline.len() * 3,
        ok,
        located
    ));
    assert!(located > 0, "expected at least some mutations to be rejected");
    log.pass("no panic / no OOB across all single-byte corruptions");
}
