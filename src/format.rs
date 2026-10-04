//! Binary container format `CDCB/1` — a self-delimiting bundle of
//! manifest + chunk index + payload.
//!
//! Layout (all integers little-endian):
//!
//! ```text
//! offset  size  field
//! 0       4     magic "CDCB"
//! 4       2     format version (= 1)
//! 6       2     flags (= 0, reserved)
//! 8       4     header_len
//! 12      ..    header JSON: { manifest, chunk_count, payload_len, payload_sha256 }
//! ..      ..    entries: chunk_count * (offset u64, len u32, sha256[32])
//! ..      ..    payload bytes (payload_len)
//! ```
//!
//! The digest of each chunk is recorded so corruption is *detectable*; byte
//! equality after reassembly is still checked by the recovery kernel —
//! digests are evidence, not a substitute for the bytes.

use crate::chunker::ChunkRef;
use crate::error::{CdcError, ErrorCategory};
use crate::manifest::Manifest;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const MAGIC: &[u8; 4] = b"CDCB";
pub const VERSION: u16 = 1;
pub const FIXED_HEADER_LEN: usize = 12;
pub const ENTRY_LEN: usize = 8 + 4 + 32;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContainerHeader {
    pub manifest: Manifest,
    pub chunk_count: u64,
    pub payload_len: u64,
    pub payload_sha256: String,
}

/// A fully parsed container. `payload` is a slice into `raw` region? No —
/// we own everything to keep the API simple and safe.
#[derive(Debug, Clone)]
pub struct Container {
    pub header: ContainerHeader,
    pub entries: Vec<ChunkRef>,
    /// Offset of the payload region inside the encoded byte stream.
    pub payload_offset: usize,
}

/// Encode manifest + chunk index + payload into a container byte string.
pub fn encode(manifest: &Manifest, chunks: &[ChunkRef], payload: &[u8]) -> Vec<u8> {
    let header = ContainerHeader {
        manifest: manifest.clone(),
        chunk_count: chunks.len() as u64,
        payload_len: payload.len() as u64,
        payload_sha256: hex::encode(Sha256::digest(payload)),
    };
    let header_json = serde_json::to_vec(&header).expect("header serialization is infallible");

    let mut out = Vec::with_capacity(FIXED_HEADER_LEN + header_json.len() + chunks.len() * ENTRY_LEN + payload.len());
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(&VERSION.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes()); // flags
    out.extend_from_slice(&(header_json.len() as u32).to_le_bytes());
    out.extend_from_slice(&header_json);
    for c in chunks {
        out.extend_from_slice(&c.offset.to_le_bytes());
        out.extend_from_slice(&c.len.to_le_bytes());
        let digest = Sha256::digest(&payload[c.offset as usize..(c.offset + c.len as u64) as usize]);
        out.extend_from_slice(&digest);
    }
    out.extend_from_slice(payload);
    out
}

/// Parse the container structure. Does NOT verify digests — that is the
/// recovery kernel's job (`recovery` module). Structural problems map to
/// `ErrorCategory::MalformedContainer`.
pub fn decode(bytes: &[u8]) -> Result<(Container, Vec<u8>), CdcError> {
    let malformed = |msg: String| CdcError::new(ErrorCategory::MalformedContainer, msg);

    if bytes.len() < FIXED_HEADER_LEN {
        return Err(malformed(format!(
            "input is {} bytes, shorter than the {FIXED_HEADER_LEN}-byte fixed header",
            bytes.len()
        )));
    }
    if &bytes[0..4] != MAGIC {
        return Err(malformed("bad magic: not a CDCB container".to_string()));
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    if version != VERSION {
        return Err(malformed(format!(
            "unsupported container version {version} (this build speaks {VERSION})"
        )));
    }
    let header_len = u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]) as usize;
    let header_end = FIXED_HEADER_LEN
        .checked_add(header_len)
        .ok_or_else(|| malformed("header_len overflows usize".to_string()))?;
    if bytes.len() < header_end {
        return Err(malformed(format!(
            "truncated header: need {header_end} bytes, have {}",
            bytes.len()
        )));
    }
    let header: ContainerHeader = serde_json::from_slice(&bytes[FIXED_HEADER_LEN..header_end])
        .map_err(|e| malformed(format!("header JSON is not decodable: {e}")))?;

    let entries_len = (header.chunk_count as usize)
        .checked_mul(ENTRY_LEN)
        .ok_or_else(|| malformed("chunk_count overflows usize".to_string()))?;
    let entries_end = header_end
        .checked_add(entries_len)
        .ok_or_else(|| malformed("entries region overflows usize".to_string()))?;
    if bytes.len() < entries_end {
        return Err(malformed(format!(
            "truncated entries: need {entries_end} bytes, have {}",
            bytes.len()
        )));
    }

    let mut entries = Vec::with_capacity(header.chunk_count as usize);
    for i in 0..header.chunk_count as usize {
        let base = header_end + i * ENTRY_LEN;
        let offset = u64::from_le_bytes(bytes[base..base + 8].try_into().unwrap());
        let len = u32::from_le_bytes(bytes[base + 8..base + 12].try_into().unwrap());
        entries.push(ChunkRef { offset, len });
    }

    let payload_end = entries_end
        .checked_add(header.payload_len as usize)
        .ok_or_else(|| malformed("payload_len overflows usize".to_string()))?;
    if bytes.len() < payload_end {
        return Err(malformed(format!(
            "truncated payload: need {payload_end} bytes, have {}",
            bytes.len()
        )));
    }
    if bytes.len() > payload_end {
        return Err(malformed(format!(
            "trailing garbage: {} bytes after payload end",
            bytes.len() - payload_end
        )));
    }

    let payload = bytes[entries_end..payload_end].to_vec();
    Ok((
        Container {
            header,
            entries,
            payload_offset: entries_end,
        },
        payload,
    ))
}

/// Read the recorded digest of entry `i` from an encoded container.
/// Used by the recovery kernel; separated from `decode` so the digest bytes
/// stay exactly where the format put them.
pub fn entry_digest(bytes: &[u8], container: &Container, index: usize) -> [u8; 32] {
    let entries_start = container.payload_offset - container.entries.len() * ENTRY_LEN;
    let base = entries_start + index * ENTRY_LEN + 12;
    bytes[base..base + 32].try_into().expect("digest slice is 32 bytes")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunker::ChunkParams;
    use crate::chunker::chunk_buffer;

    fn sample() -> (Manifest, Vec<ChunkRef>, Vec<u8>) {
        let params = ChunkParams { min_size: 8, max_size: 64, mask_bits: 6, pattern: 0 };
        let payload: Vec<u8> = (0..1000u32).map(|i| (i % 253) as u8).collect();
        let chunks = chunk_buffer(params, &payload).unwrap();
        (Manifest::for_params(params), chunks, payload)
    }

    #[test]
    fn round_trip_preserves_everything() {
        let (manifest, chunks, payload) = sample();
        let encoded = encode(&manifest, &chunks, &payload);
        let (container, decoded_payload) = decode(&encoded).unwrap();
        assert_eq!(container.header.manifest, manifest);
        assert_eq!(container.entries, chunks);
        assert_eq!(decoded_payload, payload);
    }

    #[test]
    fn empty_payload_round_trip() {
        let params = ChunkParams::default();
        let manifest = Manifest::for_params(params);
        let encoded = encode(&manifest, &[], &[]);
        let (container, payload) = decode(&encoded).unwrap();
        assert!(container.entries.is_empty());
        assert!(payload.is_empty());
    }

    #[test]
    fn decode_failures_are_categorized() {
        let (manifest, chunks, payload) = sample();
        let good = encode(&manifest, &chunks, &payload);

        // Bad magic.
        let mut bad = good.clone();
        bad[0] = b'X';
        assert_eq!(decode(&bad).unwrap_err().category, ErrorCategory::MalformedContainer);

        // Unsupported version.
        let mut bad = good.clone();
        bad[4] = 99;
        assert_eq!(decode(&bad).unwrap_err().category, ErrorCategory::MalformedContainer);

        // Truncation at several points.
        for cut in [3, 10, good.len() / 2, good.len() - 1] {
            assert_eq!(
                decode(&good[..cut]).unwrap_err().category,
                ErrorCategory::MalformedContainer,
                "truncation at {cut} must be MalformedContainer"
            );
        }

        // Trailing garbage.
        let mut bad = good.clone();
        bad.push(0);
        assert_eq!(decode(&bad).unwrap_err().category, ErrorCategory::MalformedContainer);
    }
}
