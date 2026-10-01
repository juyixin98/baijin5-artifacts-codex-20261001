//! Bitmap-shape and non-word-length universe tests.
//!
//! These target the classic "looks right on round inputs, silently wrong on
//! edge lengths" failure: universes of size 1, 7, 8, 9, 15, 16, 17, 63, 64,
//! 65 and dirty trailing bytes.

mod common;

use common::init_tracing;
use tribool_index::bits::{bytes_for, Bitmap};
use tribool_index::error::ErrorKind;

#[test]
fn byte_shape_is_ceil_div_eight() {
    assert_eq!(bytes_for(0), 0);
    assert_eq!(bytes_for(1), 1);
    assert_eq!(bytes_for(7), 1);
    assert_eq!(bytes_for(8), 1);
    assert_eq!(bytes_for(9), 2);
    assert_eq!(bytes_for(64), 8);
    assert_eq!(bytes_for(65), 9);
}

#[test]
fn ones_never_sets_invalid_tail_bits() {
    for len in [
        0usize, 1, 2, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 100, 127, 128, 129,
    ] {
        let bm = Bitmap::ones(len);
        assert_eq!(
            bm.count_ones(),
            len,
            "ones({len}) must have exactly {len} bits"
        );
        // Raw bytes: every bit at position >= len must be zero (no phantom rows).
        for (byte_idx, &raw) in bm.as_bytes().iter().enumerate() {
            for bit in 0..8u32 {
                let idx = byte_idx * 8 + bit as usize;
                if idx >= len {
                    assert_eq!(raw & (1 << bit), 0, "phantom bit at {idx} for len {len}");
                }
            }
        }
        // Last-byte mask check.
        if len % 8 != 0 && len > 0 {
            let last = bm.as_bytes()[bytes_for(len) - 1];
            let expected_mask = (1u8 << (len % 8)) - 1;
            assert_eq!(last, expected_mask, "tail mask wrong for len {len}");
        }
    }
}

#[test]
fn dirty_input_bytes_have_tail_stripped() {
    // 5 valid bits packed in one byte; feed garbage in the top 3 bits.
    let bm = Bitmap::from_bytes(5, vec![0b1111_1111]).unwrap();
    assert_eq!(bm.as_bytes(), &[0b0001_1111]);
    assert_eq!(bm.count_ones(), 5);

    let bm = Bitmap::from_bytes(13, vec![0xFF, 0xFF]).unwrap();
    assert_eq!(bm.as_bytes(), &[0xFF, 0b0001_1111]);
    assert_eq!(bm.count_ones(), 13);
}

#[test]
fn set_indices_respects_len_not_byte_capacity() {
    let mut bm = Bitmap::zeros(10);
    bm.set(9, true);
    assert_eq!(bm.set_indices(), vec![9]);
    // Even if the byte buffer had stray high bits (simulated via from_bytes),
    // they are stripped at construction, so iteration never yields them.
    let bm = Bitmap::from_bytes(10, vec![0, 0b1111_1111]).unwrap();
    assert_eq!(bm.set_indices(), vec![8, 9]);
}

#[test]
fn logical_ops_over_different_universe_sizes_fail() {
    init_tracing();
    let sizes = [8usize, 9, 16, 17, 64, 65];
    for a in sizes {
        for b in sizes {
            if a == b {
                continue;
            }
            let x = Bitmap::ones(a);
            let y = Bitmap::ones(b);
            assert_eq!(
                x.and(&y).unwrap_err().kind,
                ErrorKind::UniverseMismatch,
                "{a} vs {b}"
            );
            assert_eq!(
                x.or(&y).unwrap_err().kind,
                ErrorKind::UniverseMismatch,
                "{a} vs {b}"
            );
            assert_eq!(
                x.and_not(&y).unwrap_err().kind,
                ErrorKind::UniverseMismatch,
                "{a} vs {b}"
            );
        }
    }
}

#[test]
fn and_not_keeps_tail_clean_and_matches_brute_force() {
    for len in [1usize, 7, 8, 9, 13, 16, 17, 65] {
        let mut a = Bitmap::ones(len);
        let mut b = Bitmap::zeros(len);
        // Alternating pattern in b.
        for i in 0..len {
            b.set(i, i % 2 == 0);
        }
        // Delete-ish: knock out one non-boundary row.
        if len > 3 {
            a.set(len - 2, false);
        }
        let out = a.and_not(&b).unwrap();
        for i in 0..len {
            let expect = a.get(i) && !b.get(i);
            assert_eq!(out.get(i), expect, "len {len} row {i}");
        }
        assert_eq!(
            out.count_ones(),
            (0..len).filter(|&i| a.get(i) && !b.get(i)).count()
        );
    }
}

#[test]
fn zero_length_bitmaps_are_valid_empty_sets() {
    let a = Bitmap::zeros(0);
    let b = Bitmap::ones(0);
    assert_eq!(a, b);
    assert_eq!(a.count_ones(), 0);
    assert!(a.set_indices().is_empty());
    assert_eq!(a.and(&b).unwrap(), a);
}
