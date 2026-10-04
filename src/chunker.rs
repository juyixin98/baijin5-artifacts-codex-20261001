//! Streaming content-defined chunker.
//!
//! The chunker keeps its rolling-hash state and the current (bounded) chunk
//! buffer across arbitrary input buffers, so the same byte stream produces
//! the same boundaries no matter how the caller splits it into `push` calls.
//!
//! Boundary rules (evaluated per byte, in this order):
//! 1. If the current chunk reached `max_size`, cut (forced split).
//! 2. Else if the current chunk has at least `min_size` bytes and
//!    `hash & mask == 0`, cut (content-defined split).
//! 3. `finish` flushes any remaining bytes as the final chunk.
//!
//! Empty input rule: chunking zero bytes yields zero chunks, `total_len = 0`
//! and the SHA-256 of the empty string. An input smaller than `min_size`
//! yields exactly one chunk.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::gear::GearHash;
use crate::params::ChunkParams;

/// Metadata of one emitted chunk. Offsets are absolute positions in the
/// original byte stream.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkMeta {
    pub offset: u64,
    pub len: u64,
    /// Hex-encoded SHA-256 of the chunk bytes. A lookup hint only — it never
    /// replaces byte-for-byte verification (see `recovery`).
    pub sha256: String,
}

/// Result of chunking a complete input.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkOutcome {
    pub chunks: Vec<ChunkMeta>,
    pub total_len: u64,
    /// Hex-encoded SHA-256 of the whole input.
    pub content_sha256: String,
}

pub struct Chunker {
    params: ChunkParams,
    hash: GearHash,
    /// Bytes of the current, not-yet-closed chunk. Bounded by `max_size`.
    buf: Vec<u8>,
    /// Absolute offset of the first byte in `buf`.
    chunk_start: u64,
    total_len: u64,
    content_hasher: Sha256,
    chunks: Vec<ChunkMeta>,
}

impl Chunker {
    pub fn new(params: ChunkParams) -> Self {
        debug_assert!(params.validate().is_ok(), "chunker requires valid params");
        Chunker {
            params,
            hash: GearHash::new(),
            buf: Vec::with_capacity(params.max_size),
            chunk_start: 0,
            total_len: 0,
            content_hasher: Sha256::new(),
            chunks: Vec::new(),
        }
    }

    /// Feed the next slice of the input. Slices may be of any size, including
    /// empty (a no-op) and one byte at a time.
    pub fn push(&mut self, data: &[u8]) {
        if data.is_empty() {
            return;
        }
        self.content_hasher.update(data);
        self.total_len += data.len() as u64;
        let mask = self.params.mask();
        for &b in data {
            self.buf.push(b);
            let h = self.hash.roll(b);
            let len = self.buf.len();
            let forced = len >= self.params.max_size;
            let content_defined = len >= self.params.min_size && (h & mask) == 0;
            if forced || content_defined {
                self.cut();
            }
        }
    }

    /// Flush the trailing partial chunk (if any) and return the outcome.
    pub fn finish(mut self) -> ChunkOutcome {
        if !self.buf.is_empty() {
            self.cut();
        }
        ChunkOutcome {
            chunks: self.chunks,
            total_len: self.total_len,
            content_sha256: hex::encode(self.content_hasher.finalize()),
        }
    }

    fn cut(&mut self) {
        let digest = Sha256::digest(&self.buf);
        self.chunks.push(ChunkMeta {
            offset: self.chunk_start,
            len: self.buf.len() as u64,
            sha256: hex::encode(digest),
        });
        self.chunk_start += self.buf.len() as u64;
        self.buf.clear();
        self.hash.reset();
    }
}

/// Convenience helper: chunk a complete in-memory input in one shot.
pub fn chunk_all(params: ChunkParams, data: &[u8]) -> ChunkOutcome {
    let mut c = Chunker::new(params);
    c.push(data);
    c.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_params() -> ChunkParams {
        ChunkParams { min_size: 64, avg_bits: 12, max_size: 1 << 14 }
    }

    fn boundaries(outcome: &ChunkOutcome) -> Vec<u64> {
        outcome.chunks.iter().map(|c| c.offset).collect()
    }

    #[test]
    fn empty_input_yields_zero_chunks() {
        let out = chunk_all(test_params(), b"");
        assert_eq!(out.total_len, 0);
        assert!(out.chunks.is_empty());
        // SHA-256 of the empty string.
        assert_eq!(
            out.content_sha256,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn tiny_input_yields_single_chunk() {
        let data = b"hello";
        let out = chunk_all(test_params(), data);
        assert_eq!(out.chunks.len(), 1);
        assert_eq!(out.chunks[0].offset, 0);
        assert_eq!(out.chunks[0].len, 5);
    }

    #[test]
    fn forced_split_at_max_size() {
        // Long run of identical bytes: the rolling hash degenerates, so only
        // max_size can produce boundaries.
        let data = vec![0xAAu8; 100_000];
        let out = chunk_all(test_params(), &data);
        for c in &out.chunks[..out.chunks.len() - 1] {
            assert_eq!(c.len, test_params().max_size as u64);
        }
        assert!(out.chunks.last().unwrap().len <= test_params().max_size as u64);
        let total: u64 = out.chunks.iter().map(|c| c.len).sum();
        assert_eq!(total, 100_000);
    }

    #[test]
    fn split_invariance() {
        // Same content, many different feed patterns -> identical boundaries.
        let data: Vec<u8> = (0..200_000u32).map(|i| i.wrapping_mul(2654435761) as u8).collect();
        let whole = chunk_all(test_params(), &data);
        for step in [1usize, 3, 7, 64, 4096, 65_537] {
            let mut c = Chunker::new(test_params());
            for piece in data.chunks(step) {
                c.push(piece);
            }
            let out = c.finish();
            assert_eq!(
                boundaries(&out),
                boundaries(&whole),
                "boundaries differ for feed step {step}"
            );
            assert_eq!(out.content_sha256, whole.content_sha256);
        }
    }

    #[test]
    fn chunks_cover_input_contiguously() {
        let data: Vec<u8> = (0..150_000u32).map(|i| (i % 251) as u8).collect();
        let out = chunk_all(test_params(), &data);
        let mut expected = 0u64;
        for c in &out.chunks {
            assert_eq!(c.offset, expected);
            expected += c.len;
        }
        assert_eq!(expected, data.len() as u64);
    }
}
