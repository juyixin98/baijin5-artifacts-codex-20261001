//! Encoding kernel: integer column -> concatenated RIB blocks.
//!
//! The encoder scans the column once, splitting it into maximal segments:
//! a segment of equal values with length >= `min_run` becomes an RLE block,
//! everything between such runs becomes a BITPACK block. Segmentation is a
//! partition — every input value lands in exactly one block, so mode switches
//! neither drop nor duplicate boundary values.

use crate::bitstream::BitWriter;
use crate::format::{
    bitpack_payload_len, required_bit_width, rle_value_bytes, BlockHeader, Mode,
};

/// Encoder tuning.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EncodeOptions {
    /// Minimum run length that is encoded as RLE; shorter repeats stay in
    /// BITPACK segments.
    pub min_run: u32,
}

impl Default for EncodeOptions {
    fn default() -> Self {
        EncodeOptions { min_run: 8 }
    }
}

/// Maximum values per BITPACK block; keeps blocks bounded so the default
/// decode budget always accepts encoder output.
pub const MAX_BITPACK_VALUES: usize = 1_000_000;

/// Encode a whole column into concatenated blocks.
pub fn encode_column(values: &[u64], opts: &EncodeOptions) -> Vec<u8> {
    let mut out = Vec::new();
    let mut lit_start = 0usize;
    let mut i = 0usize;
    while i < values.len() {
        let run_end = run_end(values, i);
        let run_len = (run_end - i) as u64;
        if run_len >= opts.min_run as u64 {
            if lit_start < i {
                encode_bitpack_segment(&values[lit_start..i], &mut out);
            }
            // Runs longer than u32::MAX are split across consecutive blocks.
            let mut remaining = run_len;
            while remaining > 0 {
                let take = remaining.min(u32::MAX as u64);
                encode_rle_block(values[i], take, &mut out);
                remaining -= take;
            }
            i = run_end;
            lit_start = i;
        } else {
            i += 1;
        }
    }
    if lit_start < values.len() {
        encode_bitpack_segment(&values[lit_start..], &mut out);
    }
    out
}

/// Encode a literal segment as one or more bounded BITPACK blocks.
fn encode_bitpack_segment(values: &[u64], out: &mut Vec<u8>) {
    for chunk in values.chunks(MAX_BITPACK_VALUES) {
        encode_bitpack_block(chunk, out);
    }
}

/// End index (exclusive) of the maximal run of equal values starting at `i`.
fn run_end(values: &[u64], i: usize) -> usize {
    let v = values[i];
    let mut j = i + 1;
    while j < values.len() && values[j] == v {
        j += 1;
    }
    j
}

/// Append one BITPACK block covering exactly `values`.
fn encode_bitpack_block(values: &[u64], out: &mut Vec<u8>) {
    debug_assert!(!values.is_empty());
    let max = values.iter().copied().max().unwrap_or(0);
    let bit_width = required_bit_width(max);
    let value_count = values.len() as u32;
    let payload_len = bitpack_payload_len(bit_width, value_count) as u32;

    let header = BlockHeader {
        mode: Mode::BitPack,
        bit_width,
        value_count,
        payload_len,
    };
    out.extend_from_slice(&header.to_bytes());

    let mut writer = BitWriter::new();
    // Full groups plus zero padding in the final partial group; padding
    // values are not valid values and are never emitted by the decoder.
    let padded = value_count.div_ceil(crate::format::BITPACK_GROUP as u32)
        as usize
        * crate::format::BITPACK_GROUP;
    for &v in values {
        writer.write_bits(v, bit_width);
    }
    for _ in values.len()..padded {
        writer.write_bits(0, bit_width);
    }
    let payload = writer.finish();
    debug_assert_eq!(payload.len(), payload_len as usize);
    out.extend_from_slice(&payload);
}

/// Append one RLE block for `run_len` copies of `value`.
/// `run_len` must fit in u32 (the caller splits longer runs across blocks).
fn encode_rle_block(value: u64, run_len: u64, out: &mut Vec<u8>) {
    debug_assert!(run_len >= 1 && run_len <= u32::MAX as u64);
    let bit_width = required_bit_width(value);
    let value_bytes = rle_value_bytes(bit_width);

    let payload_len = (4 + value_bytes) as u32;
    let header = BlockHeader {
        mode: Mode::Rle,
        bit_width,
        value_count: run_len as u32,
        payload_len,
    };
    out.extend_from_slice(&header.to_bytes());
    out.extend_from_slice(&(run_len as u32).to_le_bytes());
    out.extend_from_slice(&value.to_le_bytes()[..value_bytes]);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::budget::DecodeBudget;
    use crate::decode::decode_column;

    #[test]
    fn empty_column_encodes_to_nothing() {
        assert!(encode_column(&[], &EncodeOptions::default()).is_empty());
    }

    #[test]
    fn roundtrip_smoke() {
        let mut values: Vec<u64> = vec![7; 100];
        values.extend_from_slice(&[1, 2, 3, 4, 5]);
        values.extend_from_slice(&[u64::MAX; 9]);
        let bytes = encode_column(&values, &EncodeOptions::default());
        let decoded = decode_column(&bytes, &DecodeBudget::default()).unwrap();
        assert_eq!(decoded, values);
    }
}
