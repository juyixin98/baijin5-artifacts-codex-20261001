//! Manifest: the self-describing record of how one input was chunked.
//!
//! A manifest carries the full algorithm specification and parameters next to
//! the chunk list, so a reader can verify or reproduce the chunking without
//! out-of-band knowledge. Digests in the manifest are integrity *hints*;
//! authoritative verification always compares the original bytes
//! (see `recovery::verify_against`).

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::chunker::{ChunkMeta, ChunkOutcome};
use crate::params::{AlgorithmSpec, ChunkParams};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Manifest {
    pub algorithm: AlgorithmSpec,
    pub total_len: u64,
    /// Hex-encoded SHA-256 of the whole input.
    pub content_sha256: String,
    pub chunks: Vec<ChunkMeta>,
}

impl Manifest {
    pub fn from_outcome(params: &ChunkParams, outcome: ChunkOutcome) -> Self {
        Manifest {
            algorithm: AlgorithmSpec::from_params(params),
            total_len: outcome.total_len,
            content_sha256: outcome.content_sha256,
            chunks: outcome.chunks,
        }
    }

    /// Manifest for the empty input: zero chunks, digest of the empty string.
    pub fn empty(params: &ChunkParams) -> Self {
        Manifest {
            algorithm: AlgorithmSpec::from_params(params),
            total_len: 0,
            content_sha256: hex::encode(Sha256::digest(b"")),
            chunks: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunker::chunk_all;

    #[test]
    fn empty_manifest_matches_empty_chunking() {
        let params = ChunkParams::default_params();
        let from_chunker = Manifest::from_outcome(&params, chunk_all(params, b""));
        assert_eq!(Manifest::empty(&params), from_chunker);
    }

    #[test]
    fn manifest_records_algorithm_and_params() {
        let params = ChunkParams { min_size: 64, avg_bits: 12, max_size: 1 << 14 };
        let m = Manifest::from_outcome(&params, chunk_all(params, b"abc"));
        assert_eq!(m.algorithm.name, "gear-cdc");
        assert_eq!(m.algorithm.min_size, 64);
        assert_eq!(m.algorithm.avg_bits, 12);
        assert_eq!(m.algorithm.max_size, 1 << 14);
        assert_eq!(m.algorithm.mask_hex, "0x0000000000000fff");
        assert_eq!(m.algorithm.chunk_digest, "sha256");
    }

    #[test]
    fn manifest_json_roundtrip() {
        let params = ChunkParams::default_params();
        let m = Manifest::from_outcome(&params, chunk_all(params, b"some bytes"));
        let json = serde_json::to_string_pretty(&m).unwrap();
        let back: Manifest = serde_json::from_str(&json).unwrap();
        assert_eq!(m, back);
    }
}
