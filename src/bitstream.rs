//! LSB-first bit-level IO used by the BITPACK kernel.
//!
//! Bit 0 of a value is written to the lowest free bit position of the stream;
//! bytes fill from bit 0 upward. The reader is bounds-checked and reports
//! structured errors instead of panicking or reading out of bounds.

use crate::error::{Error, ErrorCategory, Result};

/// LSB-first bit writer.
#[derive(Debug, Default)]
pub struct BitWriter {
    bytes: Vec<u8>,
    /// Number of bits already used in the last byte (0..8).
    bit_pos: u32,
}

impl BitWriter {
    pub fn new() -> Self {
        BitWriter::default()
    }

    /// Append the low `width` bits of `value` to the stream.
    pub fn write_bits(&mut self, value: u64, width: u8) {
        debug_assert!(width <= 64);
        let mut remaining = width as u32;
        let mut shift = 0u32;
        while remaining > 0 {
            if self.bit_pos == 0 {
                self.bytes.push(0);
            }
            let free = 8 - self.bit_pos;
            let take = free.min(remaining);
            let mask = if take == 64 { u64::MAX } else { (1u64 << take) - 1 };
            let chunk = ((value >> shift) & mask) as u8;
            let last = self.bytes.last_mut().expect("byte just pushed");
            *last |= chunk << self.bit_pos;
            self.bit_pos = (self.bit_pos + take) % 8;
            shift += take;
            remaining -= take;
        }
    }

    /// Finish writing and return the byte buffer. The final byte is
    /// zero-padded in its high bits; padding bits carry no values.
    pub fn finish(self) -> Vec<u8> {
        self.bytes
    }
}

/// LSB-first, bounds-checked bit reader over a byte slice.
#[derive(Debug)]
pub struct BitReader<'a> {
    bytes: &'a [u8],
    /// Absolute bit offset of the next bit to read.
    bit_offset: usize,
    /// Base offset of `bytes` within the original input, for error reporting.
    base_offset: usize,
}

impl<'a> BitReader<'a> {
    pub fn new(bytes: &'a [u8], base_offset: usize) -> Self {
        BitReader {
            bytes,
            bit_offset: 0,
            base_offset,
        }
    }

    /// Read `width` bits (0..=64) as a u64.
    pub fn read_bits(&mut self, width: u8) -> Result<u64> {
        debug_assert!(width <= 64);
        let width = width as usize;
        if self.bit_offset + width > self.bytes.len() * 8 {
            return Err(Error::new(
                ErrorCategory::TruncatedPayload,
                self.base_offset + self.bit_offset / 8,
                format!(
                    "bit reader underrun: need {} bits at bit {}, have {} bits",
                    width,
                    self.bit_offset,
                    self.bytes.len() * 8
                ),
            ));
        }
        let mut value = 0u64;
        let mut read = 0usize;
        while read < width {
            let abs = self.bit_offset + read;
            let byte = self.bytes[abs / 8];
            let bit_in_byte = (abs % 8) as u32;
            let take = (8 - bit_in_byte as usize).min(width - read);
            let mask = ((1u16 << take) - 1) as u8;
            let chunk = (byte >> bit_in_byte) & mask;
            value |= (chunk as u64) << read;
            read += take;
        }
        self.bit_offset += width;
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn write_then_read_roundtrip_mixed_widths() {
        let mut w = BitWriter::new();
        w.write_bits(0b101, 3);
        w.write_bits(u64::MAX, 64);
        w.write_bits(0, 0);
        w.write_bits(7, 3);
        let bytes = w.finish();

        let mut r = BitReader::new(&bytes, 0);
        assert_eq!(r.read_bits(3).unwrap(), 0b101);
        assert_eq!(r.read_bits(64).unwrap(), u64::MAX);
        assert_eq!(r.read_bits(0).unwrap(), 0);
        assert_eq!(r.read_bits(3).unwrap(), 7);
    }

    #[test]
    fn reader_underrun_is_structured_error() {
        let bytes = [0xFFu8];
        let mut r = BitReader::new(&bytes, 16);
        let err = r.read_bits(9).unwrap_err();
        assert_eq!(err.category, ErrorCategory::TruncatedPayload);
        assert_eq!(err.offset, 16); // underrun located at the first unread byte
    }
}
