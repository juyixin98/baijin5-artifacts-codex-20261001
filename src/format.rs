//! Binary block format: header layout, modes, and fixed legality rules.
//!
//! See `docs/FORMAT.md` for the normative specification.

/// Magic bytes at offset 0 of every block.
pub const MAGIC: [u8; 4] = *b"RIB1";
/// Only format version currently supported.
pub const VERSION: u8 = 1;
/// Fixed block header length in bytes.
pub const HEADER_LEN: usize = 16;
/// Maximum legal bit width (full u64).
pub const MAX_BIT_WIDTH: u8 = 64;
/// Bit-pack group size: values are packed in groups of 8.
pub const BITPACK_GROUP: usize = 8;

/// Encoding mode declared in the block header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Values packed LSB-first in groups of 8.
    BitPack,
    /// Run-length encoding of (run_len, value) pairs.
    Rle,
}

impl Mode {
    pub fn from_tag(tag: u8) -> Option<Mode> {
        match tag {
            0 => Some(Mode::BitPack),
            1 => Some(Mode::Rle),
            _ => None,
        }
    }

    pub fn tag(self) -> u8 {
        match self {
            Mode::BitPack => 0,
            Mode::Rle => 1,
        }
    }
}

/// Minimal bit width needed to represent `value`; 0 iff `value == 0`.
pub fn required_bit_width(value: u64) -> u8 {
    if value == 0 {
        0
    } else {
        (u64::BITS - value.leading_zeros()) as u8
    }
}

/// Parsed and validated block header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BlockHeader {
    pub mode: Mode,
    pub bit_width: u8,
    pub value_count: u32,
    pub payload_len: u32,
}

impl BlockHeader {
    /// Serialize to the fixed 16-byte little-endian header.
    pub fn to_bytes(&self) -> [u8; HEADER_LEN] {
        let mut out = [0u8; HEADER_LEN];
        out[0..4].copy_from_slice(&MAGIC);
        out[4] = VERSION;
        out[5] = self.mode.tag();
        out[6] = self.bit_width;
        out[7] = 0;
        out[8..12].copy_from_slice(&self.value_count.to_le_bytes());
        out[12..16].copy_from_slice(&self.payload_len.to_le_bytes());
        out
    }
}

/// Expected BITPACK payload length in bytes for a valid block.
///
/// Groups of [`BITPACK_GROUP`] values, `bit_width` bytes per group; the final
/// partial group is zero-padded and the padding is not part of `value_count`.
/// RLE payload length is intentionally not derivable from the header alone —
/// the decoder validates it while walking runs.
pub fn bitpack_payload_len(bit_width: u8, value_count: u32) -> u64 {
    let groups = (value_count as u64).div_ceil(BITPACK_GROUP as u64);
    groups * bit_width as u64
}

/// Bytes per stored value in an RLE run.
pub fn rle_value_bytes(bit_width: u8) -> usize {
    bit_width.div_ceil(8) as usize
}
