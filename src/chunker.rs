//! Streaming content-defined chunker built on a 64-byte gear rolling hash.
//!
//! Algorithm `gear64-cdc-v1`:
//!   h = 0 at the start of every chunk (the hash is reset at each cut).
//!   For each byte b: h = (h << 1) + GEAR[b]  (wrapping, 64-bit).
//!   A boundary is declared after a byte when the current chunk has reached
//!   `min_size` bytes AND either
//!     - the chunk has reached `max_size` bytes (forced cut), or
//!     - (h & mask) == pattern   (content-defined cut)
//!   where mask = (1 << mask_bits) - 1.
//!   `finish()` emits any trailing partial chunk.
//!   Empty input yields zero chunks.
//!
//! Because h only depends on the bytes since the last cut, boundaries are a
//! pure function of content: feeding the same bytes in any segmentation
//! produces the same chunk boundaries.

use crate::error::{CdcError, ErrorCategory};
use crate::gear::GEAR;
use serde::{Deserialize, Serialize};

/// Effective hash window in bytes (one bit shifted out per byte, 64-bit word).
pub const WINDOW_BYTES: usize = 64;

/// Algorithm identifier written into every manifest.
pub const ALGORITHM_ID: &str = "gear64-cdc-v1";

/// Chunking parameters. Serialized into the manifest so a decoder can
/// confirm it speaks the same algorithm before trusting boundaries.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkParams {
    /// Minimum chunk size in bytes; no content-defined cut before this.
    pub min_size: usize,
    /// Maximum chunk size in bytes; forced cut at this length.
    pub max_size: usize,
    /// Number of low hash bits tested against `pattern`.
    pub mask_bits: u32,
    /// Target boundary pattern: cut when (hash & mask) == pattern.
    pub pattern: u64,
}

impl Default for ChunkParams {
    fn default() -> Self {
        Self {
            min_size: 2048,
            max_size: 65536,
            mask_bits: 13,
            pattern: 0,
        }
    }
}

impl ChunkParams {
    pub fn mask(&self) -> u64 {
        debug_assert!(self.mask_bits < 64);
        (1u64 << self.mask_bits) - 1
    }

    /// Validate parameters; returns a categorized error on any violation.
    pub fn validate(&self) -> Result<(), CdcError> {
        if self.min_size == 0 {
            return Err(CdcError::new(
                ErrorCategory::InvalidParams,
                "min_size must be >= 1",
            ));
        }
        if self.min_size > self.max_size {
            return Err(CdcError::new(
                ErrorCategory::InvalidParams,
                format!("min_size ({}) must be <= max_size ({})", self.min_size, self.max_size),
            ));
        }
        if self.mask_bits == 0 || self.mask_bits >= 64 {
            return Err(CdcError::new(
                ErrorCategory::InvalidParams,
                format!("mask_bits ({}) must be in 1..=63", self.mask_bits),
            ));
        }
        if self.pattern & !self.mask() != 0 {
            return Err(CdcError::new(
                ErrorCategory::InvalidParams,
                format!(
                    "pattern ({:#x}) has bits outside mask ({:#x})",
                    self.pattern,
                    self.mask()
                ),
            ));
        }
        Ok(())
    }
}

/// A chunk as (offset, len) into the input stream. Bytes themselves are
/// never stored here; digests are computed by the caller when needed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkRef {
    pub offset: u64,
    pub len: u32,
}

/// Streaming chunker. State between `feed` calls is exactly: the rolling
/// hash, the length of the open chunk, and the global offset — so memory is
/// O(1) regardless of input size.
#[derive(Debug)]
pub struct Chunker {
    params: ChunkParams,
    mask: u64,
    hash: u64,
    chunk_len: usize,
    offset: u64,
}

impl Chunker {
    pub fn new(params: ChunkParams) -> Result<Self, CdcError> {
        params.validate()?;
        Ok(Self {
            params,
            mask: params.mask(),
            hash: 0,
            chunk_len: 0,
            offset: 0,
        })
    }

    /// Feed the next slice of input. Returns references to every chunk
    /// completed by these bytes. Chunks still open at end of input are
    /// returned by `finish`.
    pub fn feed(&mut self, buf: &[u8]) -> Vec<ChunkRef> {
        let mut out = Vec::new();
        for &b in buf {
            self.hash = self.hash.wrapping_shl(1).wrapping_add(GEAR[b as usize]);
            self.chunk_len += 1;
            self.offset += 1;
            if self.chunk_len >= self.params.min_size
                && (self.chunk_len >= self.params.max_size
                    || (self.hash & self.mask) == self.params.pattern)
            {
                out.push(ChunkRef {
                    offset: self.offset - self.chunk_len as u64,
                    len: self.chunk_len as u32,
                });
                self.hash = 0;
                self.chunk_len = 0;
            }
        }
        out
    }

    /// Flush the trailing partial chunk, if any. Empty input overall yields
    /// an empty chunk list — this is the explicit empty-input rule.
    pub fn finish(&mut self) -> Vec<ChunkRef> {
        if self.chunk_len == 0 {
            return Vec::new();
        }
        let c = ChunkRef {
            offset: self.offset - self.chunk_len as u64,
            len: self.chunk_len as u32,
        };
        self.hash = 0;
        self.chunk_len = 0;
        vec![c]
    }
}

/// Convenience: chunk a whole in-memory buffer in one shot.
pub fn chunk_buffer(params: ChunkParams, data: &[u8]) -> Result<Vec<ChunkRef>, CdcError> {
    let mut c = Chunker::new(params)?;
    let mut out = c.feed(data);
    out.extend(c.finish());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params() -> ChunkParams {
        ChunkParams {
            min_size: 8,
            max_size: 64,
            mask_bits: 6,
            pattern: 0,
        }
    }

    #[test]
    fn empty_input_yields_zero_chunks() {
        let chunks = chunk_buffer(params(), b"").unwrap();
        assert!(chunks.is_empty());
    }

    #[test]
    fn input_shorter_than_min_yields_single_chunk() {
        let data = b"abc";
        let chunks = chunk_buffer(params(), data).unwrap();
        assert_eq!(chunks, vec![ChunkRef { offset: 0, len: 3 }]);
    }

    #[test]
    fn max_size_forces_cut() {
        // Constant bytes: hash pattern may or may not hit, but max_size must.
        let data = vec![0xABu8; 1000];
        let chunks = chunk_buffer(params(), &data).unwrap();
        assert!(chunks.iter().all(|c| c.len as usize <= 64));
        assert!(chunks.iter().any(|c| c.len == 64));
        let total: u64 = chunks.iter().map(|c| c.len as u64).sum();
        assert_eq!(total, 1000);
    }

    #[test]
    fn segmentation_invariance() {
        // Same content, many different feed segmentations -> identical boundaries.
        let data: Vec<u8> = (0..5000u32).map(|i| (i.wrapping_mul(2654435761) >> 13) as u8).collect();
        let whole = chunk_buffer(params(), &data).unwrap();
        for step in [1usize, 3, 7, 64, 1024] {
            let mut c = Chunker::new(params()).unwrap();
            let mut got = Vec::new();
            for slice in data.chunks(step) {
                got.extend(c.feed(slice));
            }
            got.extend(c.finish());
            assert_eq!(whole, got, "segmentation step {step} changed boundaries");
        }
    }

    #[test]
    fn chunks_tile_input_contiguously() {
        let data: Vec<u8> = (0..3000u32).map(|i| (i % 251) as u8).collect();
        let chunks = chunk_buffer(params(), &data).unwrap();
        let mut pos = 0u64;
        for c in &chunks {
            assert_eq!(c.offset, pos);
            pos += c.len as u64;
        }
        assert_eq!(pos, data.len() as u64);
    }

    #[test]
    fn invalid_params_are_rejected_with_category() {
        let bad = ChunkParams { min_size: 0, ..params() };
        let err = Chunker::new(bad).unwrap_err();
        assert_eq!(err.category, ErrorCategory::InvalidParams);

        let bad = ChunkParams { min_size: 100, max_size: 10, ..params() };
        assert_eq!(Chunker::new(bad).unwrap_err().category, ErrorCategory::InvalidParams);

        let bad = ChunkParams { mask_bits: 0, ..params() };
        assert_eq!(Chunker::new(bad).unwrap_err().category, ErrorCategory::InvalidParams);

        let bad = ChunkParams { mask_bits: 4, pattern: 0xFF, ..params() };
        assert_eq!(Chunker::new(bad).unwrap_err().category, ErrorCategory::InvalidParams);
    }
}
