//! Encoder kernel: segments a u64 column into RLE and bit-pack blocks.
//!
//! Strategy (deterministic, mirrored by `tools/gen_fixtures.py`):
//!   1. Segment the column into maximal runs of equal values.
//!   2. A run of length >= `rle_min_run` becomes one RLE block.
//!   3. Shorter runs accumulate into a literal buffer; the buffer is flushed
//!      as one bit-pack block whenever a long run (or the end of input)
//!      arrives. The tail group is zero-padded to a multiple of 8 values;
//!      padding is never counted in `value_count`.
//!
//! Because segmentation happens *before* mode selection, a value that starts
//! a long run can never be stranded in the literal buffer — mode switches
//! cannot drop or duplicate boundary values.

use crate::error::{CodecError, ErrorCategory};
use crate::format::{
    bit_width_of, pack_group_values, BlockHeader, Mode, GROUP_LEN, MAGIC,
};

/// Encoder tuning knobs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize)]
pub struct EncoderConfig {
    /// Minimum run length that justifies an RLE block. Default 8 (one group).
    #[serde(default = "default_rle_min_run")]
    pub rle_min_run: usize,
}

pub const fn default_rle_min_run() -> usize {
    GROUP_LEN
}

impl Default for EncoderConfig {
    fn default() -> Self {
        EncoderConfig {
            rle_min_run: default_rle_min_run(),
        }
    }
}

/// Summary of one encode pass, surfaced in logs and API responses.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct EncodeStats {
    pub input_values: usize,
    pub blocks: usize,
    pub rle_blocks: usize,
    pub bitpack_blocks: usize,
    pub output_bytes: usize,
}

/// Encode `values` into a full stream (magic + blocks).
pub fn encode_column(values: &[u64], cfg: &EncoderConfig) -> Result<Vec<u8>, CodecError> {
    if values.is_empty() {
        return Err(CodecError::new(
            ErrorCategory::EmptyInput,
            "cannot encode an empty column",
        ));
    }
    let mut out = Vec::new();
    out.extend_from_slice(&MAGIC);
    encode_blocks(values, cfg, &mut out)?;
    Ok(out)
}

/// Encode with statistics (used by the service layer for observability).
pub fn encode_column_with_stats(
    values: &[u64],
    cfg: &EncoderConfig,
) -> Result<(Vec<u8>, EncodeStats), CodecError> {
    if values.is_empty() {
        return Err(CodecError::new(
            ErrorCategory::EmptyInput,
            "cannot encode an empty column",
        ));
    }
    let mut out = Vec::new();
    out.extend_from_slice(&MAGIC);
    let mut stats = encode_blocks(values, cfg, &mut out)?;
    stats.output_bytes = out.len();
    Ok((out, stats))
}

fn encode_blocks(
    values: &[u64],
    cfg: &EncoderConfig,
    out: &mut Vec<u8>,
) -> Result<EncodeStats, CodecError> {
    let mut stats = EncodeStats {
        input_values: values.len(),
        blocks: 0,
        rle_blocks: 0,
        bitpack_blocks: 0,
        output_bytes: 0,
    };
    let mut lit_buf: Vec<u64> = Vec::new();
    let mut i = 0usize;
    while i < values.len() {
        // Maximal run of equal values starting at i.
        let run_value = values[i];
        let mut run_end = i + 1;
        while run_end < values.len() && values[run_end] == run_value {
            run_end += 1;
        }
        let run_len = run_end - i;

        if run_len >= cfg.rle_min_run {
            flush_literals(&mut lit_buf, out, &mut stats)?;
            emit_rle(run_value, run_len, out, &mut stats);
        } else {
            lit_buf.extend_from_slice(&values[i..run_end]);
        }
        i = run_end;
    }
    flush_literals(&mut lit_buf, out, &mut stats)?;
    Ok(stats)
}

fn emit_rle(value: u64, count: usize, out: &mut Vec<u8>, stats: &mut EncodeStats) {
    let bw = bit_width_of(value);
    let header = BlockHeader {
        mode: Mode::Rle,
        bit_width: bw,
        value_count: count as u32,
    };
    out.extend_from_slice(&header.to_bytes());
    out.extend_from_slice(&value.to_le_bytes()[..header.rle_body_len()]);
    stats.blocks += 1;
    stats.rle_blocks += 1;
}

fn flush_literals(
    lit_buf: &mut Vec<u64>,
    out: &mut Vec<u8>,
    stats: &mut EncodeStats,
) -> Result<(), CodecError> {
    if lit_buf.is_empty() {
        return Ok(());
    }
    let count = lit_buf.len();
    let max_value = lit_buf.iter().copied().max().unwrap_or(0);
    let bw = bit_width_of(max_value);
    let header = BlockHeader {
        mode: Mode::BitPack,
        bit_width: bw,
        value_count: count as u32,
    };
    out.extend_from_slice(&header.to_bytes());

    // Pad the tail group with zeros; padding is not part of value_count.
    let padded_len = count.div_ceil(GROUP_LEN) * GROUP_LEN;
    lit_buf.resize(padded_len, 0);
    out.extend_from_slice(&pack_group_values(lit_buf, bw));
    lit_buf.clear();

    stats.blocks += 1;
    stats.bitpack_blocks += 1;
    Ok(())
}
