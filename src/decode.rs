//! Decoder kernel with strict pre-decode budget validation.
//!
//! For every block the decoder:
//!   1. checks the 8-byte header fits in the remaining input,
//!   2. validates mode / reserved / bit_width,
//!   3. computes the declared body length with checked arithmetic and
//!      verifies it against the remaining bytes *before* touching the body,
//!   4. enforces the caller-supplied [`DecodeBudget`],
//!   5. only then decodes values.
//!
//! A corrupted header therefore yields a located error (block index + byte
//! offset), never an out-of-bounds read or a panic.

use crate::error::CodecError;
use crate::format::{
    unpack_values, BlockHeader, Mode, FORMAT_VERSION, HEADER_LEN, MAGIC, MAX_BIT_WIDTH,
};

/// Resource limits applied during decoding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize)]
pub struct DecodeBudget {
    /// Maximum number of blocks in one stream.
    #[serde(default = "default_max_blocks")]
    pub max_blocks: usize,
    /// Maximum total decoded values.
    #[serde(default = "default_max_values")]
    pub max_values: usize,
    /// Maximum stream size in bytes.
    #[serde(default = "default_max_bytes")]
    pub max_bytes: usize,
}

const fn default_max_blocks() -> usize {
    1 << 16
}
const fn default_max_values() -> usize {
    1 << 24
}
const fn default_max_bytes() -> usize {
    1 << 26
}

impl Default for DecodeBudget {
    fn default() -> Self {
        DecodeBudget {
            max_blocks: default_max_blocks(),
            max_values: default_max_values(),
            max_bytes: default_max_bytes(),
        }
    }
}

/// Summary of one decode pass, surfaced in logs and API responses.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct DecodeStats {
    pub format_version: u8,
    pub blocks: usize,
    pub rle_blocks: usize,
    pub bitpack_blocks: usize,
    pub decoded_values: usize,
    pub consumed_bytes: usize,
}

/// Decode a full stream, returning values and statistics.
pub fn decode_column(
    bytes: &[u8],
    budget: &DecodeBudget,
) -> Result<(Vec<u64>, DecodeStats), CodecError> {
    if bytes.len() > budget.max_bytes {
        return Err(CodecError::budget_exceeded(
            "stream-bytes",
            budget.max_bytes,
            bytes.len(),
        ));
    }
    if bytes.len() < MAGIC.len() {
        return Err(CodecError::new(
            crate::error::ErrorCategory::TruncatedHeader,
            format!("stream shorter than {}-byte magic", MAGIC.len()),
        )
        .at(0, 0));
    }
    if bytes[..4] != MAGIC {
        let mut found = [0u8; 4];
        found.copy_from_slice(&bytes[..4]);
        return Err(CodecError::bad_magic(found));
    }

    let mut values = Vec::new();
    let mut stats = DecodeStats {
        format_version: FORMAT_VERSION,
        blocks: 0,
        rle_blocks: 0,
        bitpack_blocks: 0,
        decoded_values: 0,
        consumed_bytes: MAGIC.len(),
    };
    let mut pos = MAGIC.len();
    let mut block_index = 0usize;

    while pos < bytes.len() {
        if stats.blocks >= budget.max_blocks {
            return Err(CodecError::budget_exceeded(
                "block-count",
                budget.max_blocks,
                stats.blocks + 1,
            )
            .at(block_index, pos));
        }
        let header = read_header(bytes, pos, block_index)?;
        let body_len = header.body_len().ok_or_else(|| {
            CodecError::new(
                crate::error::ErrorCategory::BudgetExceeded,
                "declared body length overflows addressable size",
            )
            .at(block_index, pos)
        })?;

        // Pre-decode validation: the declared body must fit the real input.
        let body_start = pos + HEADER_LEN;
        let remaining = bytes.len() - body_start;
        if remaining < body_len {
            return Err(CodecError::truncated_body(
                block_index,
                body_start,
                body_len,
                remaining,
            ));
        }

        // Budget check before allocation.
        let new_total = values.len() + header.value_count as usize;
        if new_total > budget.max_values {
            return Err(CodecError::budget_exceeded(
                "value-count",
                budget.max_values,
                new_total,
            )
            .at(block_index, pos));
        }

        let body = &bytes[body_start..body_start + body_len];
        match header.mode {
            Mode::Rle => {
                let value = read_rle_value(body, header.bit_width);
                values.resize(values.len() + header.value_count as usize, value);
                stats.rle_blocks += 1;
            }
            Mode::BitPack => {
                // Slots in the final group beyond value_count are padding:
                // decode only the declared count, ignore the rest.
                let decoded = unpack_values(body, header.bit_width, header.value_count as usize);
                values.extend_from_slice(&decoded);
                stats.bitpack_blocks += 1;
            }
        }

        stats.blocks += 1;
        stats.decoded_values = values.len();
        pos = body_start + body_len;
        stats.consumed_bytes = pos;
        block_index += 1;
    }

    if stats.blocks == 0 {
        return Err(CodecError::new(
            crate::error::ErrorCategory::EmptyInput,
            "stream contains no blocks",
        )
        .at(0, MAGIC.len()));
    }
    Ok((values, stats))
}

fn read_header(bytes: &[u8], pos: usize, block_index: usize) -> Result<BlockHeader, CodecError> {
    let remaining = bytes.len() - pos;
    if remaining < HEADER_LEN {
        return Err(CodecError::truncated_header(block_index, pos, remaining));
    }
    let h = &bytes[pos..pos + HEADER_LEN];
    let mode = Mode::from_tag(h[0])
        .ok_or_else(|| CodecError::invalid_mode(block_index, pos, h[0]))?;
    let bit_width = h[1];
    if bit_width > MAX_BIT_WIDTH {
        return Err(CodecError::invalid_bit_width(block_index, pos, bit_width));
    }
    let reserved = u16::from_le_bytes([h[2], h[3]]);
    if reserved != 0 {
        return Err(CodecError::invalid_reserved(block_index, pos, reserved));
    }
    let value_count = u32::from_le_bytes([h[4], h[5], h[6], h[7]]);
    Ok(BlockHeader {
        mode,
        bit_width,
        value_count,
    })
}

fn read_rle_value(body: &[u8], bit_width: u8) -> u64 {
    let nbytes = (bit_width as usize).div_ceil(8);
    let mut buf = [0u8; 8];
    buf[..nbytes].copy_from_slice(&body[..nbytes]);
    u64::from_le_bytes(buf)
}
