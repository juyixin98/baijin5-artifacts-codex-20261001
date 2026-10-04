//! Decoding kernel: concatenated RIB blocks -> integer column.
//!
//! Order of operations for every block (see docs/FORMAT.md):
//! 1. validate header fields (magic, version, mode, reserved, bit width);
//! 2. check declared value_count / payload_len against the budget;
//! 3. recompute the expected payload length and compare with the declaration
//!    (BITPACK exactly; RLE by walking runs);
//! 4. only then read payload bytes — every read is bounds-checked and every
//!    failure carries the byte offset at which it was located.

use crate::bitstream::BitReader;
use crate::budget::DecodeBudget;
use crate::error::{Error, ErrorCategory, Result};
use crate::format::{
    bitpack_payload_len, rle_value_bytes, BlockHeader, Mode, MAGIC, MAX_BIT_WIDTH, VERSION,
};
use crate::format::{BITPACK_GROUP, HEADER_LEN};

/// Decode a whole column (a sequence of concatenated blocks).
pub fn decode_column(bytes: &[u8], budget: &DecodeBudget) -> Result<Vec<u64>> {
    let mut values = Vec::new();
    let mut offset = 0usize;
    while offset < bytes.len() {
        let (mut block_values, consumed) = decode_block(&bytes[offset..], budget)?;
        let total = values.len() as u64 + block_values.len() as u64;
        if total > budget.max_total_values {
            return Err(Error::new(
                ErrorCategory::ValueCountOverBudget,
                offset,
                format!(
                    "column would hold {} values, budget allows {}",
                    total, budget.max_total_values
                ),
            ));
        }
        values.append(&mut block_values);
        offset += consumed;
    }
    Ok(values)
}

/// Decode one block at the start of `bytes`.
/// Returns the decoded values and the number of bytes consumed.
pub fn decode_block(bytes: &[u8], budget: &DecodeBudget) -> Result<(Vec<u64>, usize)> {
    let header = parse_header(bytes)?;
    check_budget(&header, budget)?;

    let payload_len = header.payload_len as usize;
    if bytes.len() < HEADER_LEN + payload_len {
        return Err(Error::new(
            ErrorCategory::TruncatedPayload,
            bytes.len(),
            format!(
                "payload declared {} bytes, only {} available",
                payload_len,
                bytes.len() - HEADER_LEN
            ),
        ));
    }
    let payload = &bytes[HEADER_LEN..HEADER_LEN + payload_len];

    let values = match header.mode {
        Mode::BitPack => decode_bitpack(&header, payload)?,
        Mode::Rle => decode_rle(&header, payload, budget)?,
    };
    Ok((values, HEADER_LEN + payload_len))
}

/// Parse and validate the fixed 16-byte header. No payload is touched here.
fn parse_header(bytes: &[u8]) -> Result<BlockHeader> {
    if bytes.len() < HEADER_LEN {
        return Err(Error::new(
            ErrorCategory::TruncatedHeader,
            bytes.len(),
            format!("header needs {} bytes, got {}", HEADER_LEN, bytes.len()),
        ));
    }
    if bytes[0..4] != MAGIC {
        return Err(Error::new(
            ErrorCategory::BadMagic,
            0,
            format!("expected magic RIB1, got {:02x?}", &bytes[0..4]),
        ));
    }
    if bytes[4] != VERSION {
        return Err(Error::new(
            ErrorCategory::UnsupportedVersion,
            4,
            format!("unsupported version {}", bytes[4]),
        ));
    }
    let mode = Mode::from_tag(bytes[5]).ok_or_else(|| {
        Error::new(
            ErrorCategory::UnknownMode,
            5,
            format!("unknown mode tag {}", bytes[5]),
        )
    })?;
    let bit_width = bytes[6];
    if bit_width > MAX_BIT_WIDTH {
        return Err(Error::new(
            ErrorCategory::InvalidBitWidth,
            6,
            format!("bit width {} exceeds maximum {}", bit_width, MAX_BIT_WIDTH),
        ));
    }
    if bytes[7] != 0 {
        return Err(Error::new(
            ErrorCategory::NonZeroReserved,
            7,
            format!("reserved byte must be 0, got {}", bytes[7]),
        ));
    }
    let value_count = u32::from_le_bytes(bytes[8..12].try_into().unwrap());
    let payload_len = u32::from_le_bytes(bytes[12..16].try_into().unwrap());
    Ok(BlockHeader {
        mode,
        bit_width,
        value_count,
        payload_len,
    })
}

/// Budget and length-declaration checks, before any payload byte is read.
fn check_budget(header: &BlockHeader, budget: &DecodeBudget) -> Result<()> {
    if header.value_count as u64 > budget.max_values_per_block {
        return Err(Error::new(
            ErrorCategory::ValueCountOverBudget,
            8,
            format!(
                "value_count {} exceeds budget {}",
                header.value_count, budget.max_values_per_block
            ),
        ));
    }
    if header.payload_len as u64 > budget.max_payload_bytes {
        return Err(Error::new(
            ErrorCategory::PayloadLenOverBudget,
            12,
            format!(
                "payload_len {} exceeds budget {}",
                header.payload_len, budget.max_payload_bytes
            ),
        ));
    }
    if header.mode == Mode::BitPack {
        let expected = bitpack_payload_len(header.bit_width, header.value_count);
        if expected != header.payload_len as u64 {
            return Err(Error::new(
                ErrorCategory::PayloadLenMismatch,
                12,
                format!(
                    "bitpack payload for {} values at width {} must be {} bytes, declared {}",
                    header.value_count, header.bit_width, expected, header.payload_len
                ),
            ));
        }
    }
    Ok(())
}

/// Decode a BITPACK payload. Only the first `value_count` values are
/// emitted; final-group padding is skipped.
fn decode_bitpack(header: &BlockHeader, payload: &[u8]) -> Result<Vec<u64>> {
    let count = header.value_count as usize;
    if header.bit_width == 0 {
        return Ok(vec![0u64; count]);
    }
    let mut values = Vec::with_capacity(count);
    let mut reader = BitReader::new(payload, HEADER_LEN);
    for group in 0..count.div_ceil(BITPACK_GROUP) {
        let in_group = BITPACK_GROUP.min(count - group * BITPACK_GROUP);
        for _ in 0..in_group {
            values.push(reader.read_bits(header.bit_width)?);
        }
        // Skip zero padding in the final partial group: not valid values.
        for _ in in_group..BITPACK_GROUP {
            reader.read_bits(header.bit_width)?;
        }
    }
    Ok(values)
}

/// Decode an RLE payload, validating run structure and exact length.
fn decode_rle(header: &BlockHeader, payload: &[u8], budget: &DecodeBudget) -> Result<Vec<u64>> {
    let value_bytes = rle_value_bytes(header.bit_width);
    let run_bytes = 4 + value_bytes;
    let mut values = Vec::with_capacity(header.value_count as usize);
    let mut offset = 0usize;
    let mut runs = 0u64;
    let mut total = 0u64;

    while total < header.value_count as u64 || offset < payload.len() {
        if offset + run_bytes > payload.len() {
            return Err(Error::new(
                ErrorCategory::TruncatedPayload,
                HEADER_LEN + offset,
                format!(
                    "run {} needs {} bytes, {} remain in payload",
                    runs,
                    run_bytes,
                    payload.len() - offset
                ),
            ));
        }
        if runs >= budget.max_runs_per_block {
            return Err(Error::new(
                ErrorCategory::RunCountOverBudget,
                HEADER_LEN + offset,
                format!("run count exceeds budget {}", budget.max_runs_per_block),
            ));
        }
        let run_len = u32::from_le_bytes(payload[offset..offset + 4].try_into().unwrap());
        if run_len == 0 {
            return Err(Error::new(
                ErrorCategory::RunLengthZero,
                HEADER_LEN + offset,
                "run_len must be >= 1".to_string(),
            ));
        }
        let mut value = 0u64;
        for (i, &b) in payload[offset + 4..offset + run_bytes].iter().enumerate() {
            value |= (b as u64) << (8 * i);
        }
        total += run_len as u64;
        if total > header.value_count as u64 {
            return Err(Error::new(
                ErrorCategory::RunLengthSumMismatch,
                HEADER_LEN + offset,
                format!(
                    "run lengths sum to {}, exceeding declared value_count {}",
                    total, header.value_count
                ),
            ));
        }
        values.extend(std::iter::repeat_n(value, run_len as usize));
        runs += 1;
        offset += run_bytes;
    }

    if total != header.value_count as u64 {
        return Err(Error::new(
            ErrorCategory::RunLengthSumMismatch,
            HEADER_LEN + payload.len(),
            format!(
                "run lengths sum to {}, declared value_count {}",
                total, header.value_count
            ),
        ));
    }
    if offset != payload.len() {
        return Err(Error::new(
            ErrorCategory::PayloadLenMismatch,
            HEADER_LEN + offset,
            format!(
                "{} payload bytes unconsumed after {} runs",
                payload.len() - offset,
                runs
            ),
        ));
    }
    Ok(values)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::encode::{encode_column, EncodeOptions};

    #[test]
    fn rejects_short_header() {
        let err = decode_column(&[0u8; 5], &DecodeBudget::default()).unwrap_err();
        assert_eq!(err.category, ErrorCategory::TruncatedHeader);
    }

    #[test]
    fn rejects_bad_magic_at_offset_zero() {
        let mut bytes = encode_column(&[1, 2, 3], &EncodeOptions::default());
        bytes[0] = b'X';
        let err = decode_column(&bytes, &DecodeBudget::default()).unwrap_err();
        assert_eq!(err.category, ErrorCategory::BadMagic);
        assert_eq!(err.offset, 0);
    }
}
