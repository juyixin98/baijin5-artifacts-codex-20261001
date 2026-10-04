//! Recovery kernel: reassemble an input from a manifest plus its bytes, and
//! verify integrity.
//!
//! Two layers, deliberately separate:
//! - Digest checks (per-chunk and whole-content SHA-256) are fast *hints* that
//!   localize corruption.
//! - `verify_against` compares the reassembled bytes against a reference
//!   byte-for-byte. This is the authoritative check: a matching digest never
//!   substitutes for comparing the actual bytes.

use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::manifest::Manifest;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum VerifyError {
    #[error("coverage gap before chunk {index}: expected offset {expected}, found {found}")]
    CoverageGap { index: usize, expected: u64, found: u64 },
    #[error("coverage overlap at chunk {index}: expected offset {expected}, found {found}")]
    CoverageOverlap { index: usize, expected: u64, found: u64 },
    #[error("chunks cover {covered} bytes but manifest declares total_len {declared}")]
    CoverageShort { covered: u64, declared: u64 },
    #[error("chunk {index} (offset {offset}) digest mismatch")]
    ChunkDigestMismatch { index: usize, offset: u64 },
    #[error("content length mismatch: manifest {manifest} vs supplied {supplied}")]
    TotalLengthMismatch { manifest: u64, supplied: u64 },
    #[error("whole-content digest mismatch (hint only; bytes are authoritative)")]
    ContentDigestMismatch,
    #[error("byte mismatch at offset {offset}")]
    ByteMismatch { offset: u64 },
}

impl VerifyError {
    /// Stable machine-readable category for API responses and diagnostics.
    pub fn category(&self) -> &'static str {
        match self {
            VerifyError::CoverageGap { .. } => "coverage_gap",
            VerifyError::CoverageOverlap { .. } => "coverage_overlap",
            VerifyError::CoverageShort { .. } => "coverage_short",
            VerifyError::ChunkDigestMismatch { .. } => "chunk_digest_mismatch",
            VerifyError::TotalLengthMismatch { .. } => "total_length_mismatch",
            VerifyError::ContentDigestMismatch => "content_digest_mismatch",
            VerifyError::ByteMismatch { .. } => "byte_mismatch",
        }
    }
}

/// Structural check: chunks must tile `[0, total_len)` contiguously and in
/// order, with no gaps or overlaps.
pub fn check_coverage(manifest: &Manifest) -> Result<(), VerifyError> {
    let mut expected = 0u64;
    for (index, c) in manifest.chunks.iter().enumerate() {
        if c.offset > expected {
            return Err(VerifyError::CoverageGap { index, expected, found: c.offset });
        }
        if c.offset < expected {
            return Err(VerifyError::CoverageOverlap { index, expected, found: c.offset });
        }
        expected += c.len;
    }
    if expected != manifest.total_len {
        return Err(VerifyError::CoverageShort { covered: expected, declared: manifest.total_len });
    }
    Ok(())
}

/// Reassemble the original bytes from `chunk_bytes` (the concatenation of all
/// chunk payloads in order), verifying coverage and per-chunk digests.
pub fn reassemble(manifest: &Manifest, chunk_bytes: &[u8]) -> Result<Vec<u8>, VerifyError> {
    check_coverage(manifest)?;
    if chunk_bytes.len() as u64 != manifest.total_len {
        return Err(VerifyError::TotalLengthMismatch {
            manifest: manifest.total_len,
            supplied: chunk_bytes.len() as u64,
        });
    }
    for (index, c) in manifest.chunks.iter().enumerate() {
        let start = c.offset as usize;
        let end = start + c.len as usize;
        let digest = Sha256::digest(&chunk_bytes[start..end]);
        if hex::encode(digest) != c.sha256 {
            return Err(VerifyError::ChunkDigestMismatch { index, offset: c.offset });
        }
    }
    Ok(chunk_bytes.to_vec())
}

/// Authoritative verification: reassemble `chunk_bytes`, then compare the
/// result against `reference` byte-for-byte. Digest checks still run (they
/// localize corruption), but the final verdict comes from the bytes.
pub fn verify_against(
    manifest: &Manifest,
    chunk_bytes: &[u8],
    reference: &[u8],
) -> Result<(), VerifyError> {
    let bytes = reassemble(manifest, chunk_bytes)?;
    if bytes != reference {
        let offset = bytes
            .iter()
            .zip(reference.iter())
            .position(|(a, b)| a != b)
            .map(|i| i as u64)
            .unwrap_or_else(|| bytes.len().min(reference.len()) as u64);
        return Err(VerifyError::ByteMismatch { offset });
    }
    // Bytes are identical to the reference; a wrong recorded whole-content
    // digest now means the manifest itself is corrupt, not the data.
    if hex::encode(Sha256::digest(&bytes)) != manifest.content_sha256 {
        return Err(VerifyError::ContentDigestMismatch);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunker::{chunk_all, ChunkMeta};
    use crate::params::ChunkParams;

    fn params() -> ChunkParams {
        ChunkParams { min_size: 64, avg_bits: 12, max_size: 1 << 14 }
    }

    fn manifest_for(data: &[u8]) -> Manifest {
        Manifest::from_outcome(&params(), chunk_all(params(), data))
    }

    fn sample_data() -> Vec<u8> {
        (0..120_000u32).map(|i| i.wrapping_mul(1103515245).wrapping_add(12345) as u8).collect()
    }

    #[test]
    fn roundtrip_reassembles_original() {
        let data = sample_data();
        let m = manifest_for(&data);
        assert_eq!(reassemble(&m, &data).unwrap(), data);
        verify_against(&m, &data, &data).unwrap();
    }

    #[test]
    fn empty_input_verifies() {
        let m = manifest_for(b"");
        verify_against(&m, b"", b"").unwrap();
    }

    #[test]
    fn tampered_byte_gives_chunk_digest_mismatch_with_index() {
        let data = sample_data();
        let m = manifest_for(&data);
        let mut bad = data.clone();
        let at = m.chunks[1].offset as usize;
        bad[at] ^= 0xFF;
        assert_eq!(
            reassemble(&m, &bad),
            Err(VerifyError::ChunkDigestMismatch { index: 1, offset: m.chunks[1].offset })
        );
    }

    #[test]
    fn truncated_bytes_give_length_mismatch() {
        let data = sample_data();
        let m = manifest_for(&data);
        let short = &data[..data.len() - 1];
        assert_eq!(
            reassemble(&m, short),
            Err(VerifyError::TotalLengthMismatch {
                manifest: data.len() as u64,
                supplied: (data.len() - 1) as u64,
            })
        );
    }

    #[test]
    fn gap_in_manifest_is_detected() {
        let data = sample_data();
        let mut m = manifest_for(&data);
        m.chunks[1].offset += 1;
        assert!(matches!(
            check_coverage(&m),
            Err(VerifyError::CoverageGap { index: 1, .. })
        ));
    }

    #[test]
    fn overlap_in_manifest_is_detected() {
        let data = sample_data();
        let mut m = manifest_for(&data);
        m.chunks[1].offset -= 1;
        assert!(matches!(
            check_coverage(&m),
            Err(VerifyError::CoverageOverlap { index: 1, .. })
        ));
    }

    #[test]
    fn coverage_short_is_detected() {
        let data = sample_data();
        let mut m = manifest_for(&data);
        m.total_len += 5;
        assert_eq!(
            check_coverage(&m),
            Err(VerifyError::CoverageShort {
                covered: data.len() as u64,
                declared: data.len() as u64 + 5,
            })
        );
    }

    #[test]
    fn forged_chunk_digest_passes_digest_check_but_fails_byte_compare() {
        // Attacker tampers with stored chunk bytes AND rewrites the chunk
        // digest to match. Digest-only verification is fooled; comparing
        // against the reference bytes is not.
        let data = sample_data();
        let mut m = manifest_for(&data);
        let mut stored = data.clone();
        let at = m.chunks[0].offset as usize;
        stored[at] ^= 0xFF;
        let c0 = &m.chunks[0];
        let forged = Sha256::digest(&stored[c0.offset as usize..(c0.offset + c0.len) as usize]);
        m.chunks[0] = ChunkMeta { sha256: hex::encode(forged), ..c0.clone() };

        // Digest hints alone accept the tampered chunk...
        assert!(reassemble(&m, &stored).is_ok());
        // ...but byte-for-byte comparison against the original localizes it.
        assert_eq!(
            verify_against(&m, &stored, &data),
            Err(VerifyError::ByteMismatch { offset: at as u64 })
        );
    }

    #[test]
    fn corrupt_manifest_digest_is_caught_after_byte_compare() {
        // Bytes are fine but the recorded whole-content digest was tampered.
        let data = sample_data();
        let mut m = manifest_for(&data);
        m.content_sha256 = hex::encode(Sha256::digest(b"something else"));
        assert_eq!(
            verify_against(&m, &data, &data),
            Err(VerifyError::ContentDigestMismatch)
        );
    }
}
