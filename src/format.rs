//! Binary format definition for the RLE / bit-pack hybrid column stream.
//!
//! Stream layout (all integers little-endian):
//!
//! ```text
//! magic: 4 bytes, ASCII "RBP1"
//! blocks: repeated block records until EOF
//!
//! block:
//!   header, 8 bytes:
//!     u8  mode         0 = RLE, 1 = BITPACK
//!     u8  bit_width    0..=MAX_BIT_WIDTH (64)
//!     u16 reserved     must be 0
//!     u32 value_count  number of *valid* values in this block
//!   body:
//!     RLE:      ceil(bit_width/8) bytes — the single repeated value.
//!               Zero bytes when bit_width == 0 (the value is implicitly 0).
//!     BITPACK:  ceil(value_count/8) * bit_width bytes.
//!               Values are packed LSB-first in groups of 8. The final group
//!               may contain padding slots; padding is NOT part of
//!               value_count and is ignored by the decoder.
//! ```
//!
//! Legality invariants (fixed, validated before any body byte is read):
//!   * bit_width == 0 is legal for both modes (all values are 0).
//!   * bit_width == 64 is the maximum legal width.
//!   * reserved != 0, unknown mode, or bit_width > 64 corrupt the header.
//!   * The declared body length is computed with checked arithmetic and the
//!     decoder verifies the remaining budget *before* decoding a single value.

/// Stream magic, ASCII "RBP1".
pub const MAGIC: [u8; 4] = *b"RBP1";

/// Format version implemented by this crate.
pub const FORMAT_VERSION: u8 = 1;

/// Fixed block header size in bytes.
pub const HEADER_LEN: usize = 8;

/// Maximum legal bit width (a full u64).
pub const MAX_BIT_WIDTH: u8 = 64;

/// Number of values in one bit-pack group.
pub const GROUP_LEN: usize = 8;

/// Block encoding mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Run-length encoding: one value repeated `value_count` times.
    Rle,
    /// Bit-packed literals in groups of 8.
    BitPack,
}

impl Mode {
    pub fn from_tag(tag: u8) -> Option<Self> {
        match tag {
            0 => Some(Mode::Rle),
            1 => Some(Mode::BitPack),
            _ => None,
        }
    }

    pub fn tag(self) -> u8 {
        match self {
            Mode::Rle => 0,
            Mode::BitPack => 1,
        }
    }
}

/// A parsed, validated block header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BlockHeader {
    pub mode: Mode,
    pub bit_width: u8,
    pub value_count: u32,
}

impl BlockHeader {
    /// Serialize the 8-byte header.
    pub fn to_bytes(&self) -> [u8; HEADER_LEN] {
        let mut out = [0u8; HEADER_LEN];
        out[0] = self.mode.tag();
        out[1] = self.bit_width;
        // out[2..4] reserved, already zero
        out[4..8].copy_from_slice(&self.value_count.to_le_bytes());
        out
    }

    /// Bytes the RLE body occupies for this width.
    pub fn rle_body_len(&self) -> usize {
        (self.bit_width as usize).div_ceil(8)
    }

    /// Bytes the bit-pack body occupies, including tail-group padding.
    /// Uses checked arithmetic; returns None on overflow.
    pub fn bitpack_body_len(&self) -> Option<usize> {
        let groups = (self.value_count as usize).div_ceil(GROUP_LEN);
        groups.checked_mul(self.bit_width as usize)
    }

    /// Declared body length for this block's mode, checked.
    pub fn body_len(&self) -> Option<usize> {
        match self.mode {
            Mode::Rle => Some(self.rle_body_len()),
            Mode::BitPack => self.bitpack_body_len(),
        }
    }
}

/// Minimal bit width needed to represent `v` (0 for v == 0, 64 for u64::MAX).
pub fn bit_width_of(v: u64) -> u8 {
    (64 - v.leading_zeros()) as u8
}

/// Pack `values` (each must fit in `bit_width` bits) LSB-first into bytes.
/// `values.len()` must be a multiple of GROUP_LEN; callers pad tail groups.
pub fn pack_group_values(values: &[u64], bit_width: u8) -> Vec<u8> {
    debug_assert_eq!(values.len() % GROUP_LEN, 0);
    if bit_width == 0 {
        return Vec::new();
    }
    let mut out = vec![0u8; values.len() / GROUP_LEN * bit_width as usize];
    let mut bit_pos = 0usize;
    for &v in values {
        debug_assert!(bit_width == 64 || v < (1u64 << bit_width));
        for b in 0..bit_width as usize {
            if (v >> b) & 1 == 1 {
                let abs = bit_pos + b;
                out[abs / 8] |= 1 << (abs % 8);
            }
        }
        bit_pos += bit_width as usize;
    }
    out
}

/// Unpack `count` values of `bit_width` bits from LSB-first packed `bytes`.
/// Caller guarantees `bytes` covers at least `count` slots (padding included).
pub fn unpack_values(bytes: &[u8], bit_width: u8, count: usize) -> Vec<u64> {
    if bit_width == 0 {
        return vec![0u64; count];
    }
    let mut out = Vec::with_capacity(count);
    let mut bit_pos = 0usize;
    for _ in 0..count {
        let mut v = 0u64;
        for b in 0..bit_width as usize {
            let abs = bit_pos + b;
            if (bytes[abs / 8] >> (abs % 8)) & 1 == 1 {
                v |= 1 << b;
            }
        }
        out.push(v);
        bit_pos += bit_width as usize;
    }
    out
}
