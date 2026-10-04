//! Recovery kernel: given an encoded CDCB container, verify it and recover
//! the payload bytes.
//!
//! Verification layers, in order:
//!   1. structural parse (format module),
//!   2. chunk tiling: entries must cover [0, payload_len) contiguously,
//!   3. per-chunk sha256 against the recorded digests,
//!   4. whole-payload sha256 against the header,
//!   5. if an expected-original is supplied, recovered bytes are compared
//!      BYTE FOR BYTE — the digest layers above are never treated as a
//!      substitute for comparing the actual bytes.

use crate::chunker::ChunkRef;
use crate::error::{CdcError, ErrorCategory};
use crate::format;
use sha2::{Digest, Sha256};

/// Outcome of a successful recovery: the payload bytes plus the chunk map
/// that was verified against them.
#[derive(Debug)]
pub struct Recovered {
    pub payload: Vec<u8>,
    pub chunks: Vec<ChunkRef>,
    pub manifest: crate::manifest::Manifest,
}

/// Verify and recover a container. When `expected_original` is provided,
/// the recovered bytes must equal it exactly (layer 5).
pub fn recover(container_bytes: &[u8], expected_original: Option<&[u8]>) -> Result<Recovered, CdcError> {
    let (container, payload) = format::decode(container_bytes)?;

    // Layer 2: tiling.
    let mut pos = 0u64;
    for (i, c) in container.entries.iter().enumerate() {
        if c.offset != pos {
            return Err(CdcError::new(
                ErrorCategory::MalformedContainer,
                format!("chunk {i} starts at offset {} but expected {pos} (gap or overlap)", c.offset),
            ));
        }
        pos += c.len as u64;
    }
    if pos != container.header.payload_len {
        return Err(CdcError::new(
            ErrorCategory::MalformedContainer,
            format!(
                "chunks cover {pos} bytes but header declares payload_len {}",
                container.header.payload_len
            ),
        ));
    }

    // Layer 3: per-chunk digests.
    for (i, c) in container.entries.iter().enumerate() {
        let start = c.offset as usize;
        let end = start + c.len as usize;
        let actual = Sha256::digest(&payload[start..end]);
        let recorded = format::entry_digest(container_bytes, &container, i);
        if actual.as_slice() != recorded {
            return Err(CdcError::new(
                ErrorCategory::DigestMismatch,
                format!(
                    "chunk {i} (offset {}, len {}) digest mismatch: recorded {}, computed {}",
                    c.offset,
                    c.len,
                    hex::encode(recorded),
                    hex::encode(actual)
                ),
            ));
        }
    }

    // Layer 4: whole-payload digest.
    let payload_digest = hex::encode(Sha256::digest(&payload));
    if payload_digest != container.header.payload_sha256 {
        return Err(CdcError::new(
            ErrorCategory::DigestMismatch,
            format!(
                "payload digest mismatch: header {}, computed {payload_digest}",
                container.header.payload_sha256
            ),
        ));
    }

    // Layer 5: byte-for-byte comparison against the supplied original.
    if let Some(original) = expected_original {
        if payload != original {
            let first_diff = payload
                .iter()
                .zip(original.iter())
                .position(|(a, b)| a != b)
                .map(|p| p.to_string())
                .unwrap_or_else(|| "length".to_string());
            return Err(CdcError::new(
                ErrorCategory::PayloadMismatch,
                format!(
                    "digests verified but recovered bytes differ from supplied original (first difference at {first_diff})"
                ),
            ));
        }
    }

    Ok(Recovered {
        payload,
        chunks: container.entries,
        manifest: container.header.manifest,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunker::{chunk_buffer, ChunkParams};
    use crate::manifest::Manifest;

    fn sample_container(payload: &[u8]) -> Vec<u8> {
        let params = ChunkParams { min_size: 8, max_size: 64, mask_bits: 6, pattern: 0 };
        let chunks = chunk_buffer(params, payload).unwrap();
        format::encode(&Manifest::for_params(params), &chunks, payload)
    }

    #[test]
    fn honest_container_recovers_byte_identical_payload() {
        let payload: Vec<u8> = (0..5000u32).map(|i| ((i * 31 + 7) % 256) as u8).collect();
        let encoded = sample_container(&payload);
        let rec = recover(&encoded, Some(&payload)).unwrap();
        assert_eq!(rec.payload, payload);
    }

    #[test]
    fn corrupted_payload_byte_is_digest_mismatch() {
        let payload: Vec<u8> = (0..500u32).map(|i| (i % 256) as u8).collect();
        let mut encoded = sample_container(&payload);
        let last = encoded.len() - 1;
        encoded[last] ^= 0xFF;
        let err = recover(&encoded, None).unwrap_err();
        assert_eq!(err.category, ErrorCategory::DigestMismatch);
    }

    #[test]
    fn corrupted_recorded_digest_is_digest_mismatch() {
        let payload: Vec<u8> = (0..500u32).map(|i| (i % 256) as u8).collect();
        let mut encoded = sample_container(&payload);
        // First entry's digest starts right after fixed header + JSON + 12 bytes of entry.
        let header_len = u32::from_le_bytes([encoded[8], encoded[9], encoded[10], encoded[11]]) as usize;
        let digest_pos = format::FIXED_HEADER_LEN + header_len + 12;
        encoded[digest_pos] ^= 0x01;
        let err = recover(&encoded, None).unwrap_err();
        assert_eq!(err.category, ErrorCategory::DigestMismatch);
    }

    #[test]
    fn wrong_original_is_payload_mismatch_not_digest_mismatch() {
        let payload: Vec<u8> = (0..500u32).map(|i| (i % 256) as u8).collect();
        let encoded = sample_container(&payload);
        let mut other = payload.clone();
        other[10] ^= 0x01;
        let err = recover(&encoded, Some(&other)).unwrap_err();
        assert_eq!(err.category, ErrorCategory::PayloadMismatch);
    }
}
