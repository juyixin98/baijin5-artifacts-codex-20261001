//! Deterministic binary encoding of a [`Manifest`] ("CDCM" format, v1).
//!
//! Layout (all integers little-endian):
//!
//! ```text
//! offset  size  field
//! 0       4     magic "CDCM"
//! 4       2     format version (= 1)
//! 6       4     min_size (u32)
//! 10      1     avg_bits (u8)
//! 11      4     max_size (u32)
//! 15      8     total_len (u64)
//! 23      32    content_sha256 (raw bytes)
//! 55      4     chunk_count (u32)
//! 59      ...   chunk records: { offset u64, len u32, sha256 [32] } x count
//! end     32    trailer: SHA-256 of every preceding byte
//! ```
//!
//! The trailer detects truncation and corruption of the encoded form; it says
//! nothing about the chunked content itself (that is `recovery`'s job).

use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::chunker::ChunkMeta;
use crate::manifest::Manifest;
use crate::params::{AlgorithmSpec, ALGORITHM_NAME, ALGORITHM_VERSION};

pub const MAGIC: &[u8; 4] = b"CDCM";
pub const FORMAT_VERSION: u16 = 1;
const HEADER_LEN: usize = 4 + 2 + 4 + 1 + 4 + 8 + 32 + 4;
const CHUNK_RECORD_LEN: usize = 8 + 4 + 32;
const TRAILER_LEN: usize = 32;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum FormatError {
    #[error("input too short: {got} bytes, need at least {need}")]
    TooShort { got: usize, need: usize },
    #[error("bad magic: expected CDCM")]
    BadMagic,
    #[error("unsupported format version {0}")]
    UnsupportedVersion(u16),
    #[error("chunk record {index} truncated")]
    TruncatedRecord { index: usize },
    #[error("trailer digest mismatch: encoded manifest is corrupt")]
    TrailerMismatch,
    #[error("chunk digest is not 64 hex chars")]
    BadDigestHex,
    #[error("trailing {0} unexpected bytes after trailer")]
    TrailingBytes(usize),
}

pub fn encode(manifest: &Manifest) -> Vec<u8> {
    let a = &manifest.algorithm;
    let mut out = Vec::with_capacity(HEADER_LEN + manifest.chunks.len() * CHUNK_RECORD_LEN + TRAILER_LEN);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    out.extend_from_slice(&(a.min_size as u32).to_le_bytes());
    out.push(a.avg_bits as u8);
    out.extend_from_slice(&(a.max_size as u32).to_le_bytes());
    out.extend_from_slice(&manifest.total_len.to_le_bytes());
    out.extend_from_slice(&decode_digest(&manifest.content_sha256).expect("manifest digest must be hex"));
    out.extend_from_slice(&(manifest.chunks.len() as u32).to_le_bytes());
    for c in &manifest.chunks {
        out.extend_from_slice(&c.offset.to_le_bytes());
        out.extend_from_slice(&(c.len as u32).to_le_bytes());
        out.extend_from_slice(&decode_digest(&c.sha256).expect("chunk digest must be hex"));
    }
    let trailer = Sha256::digest(&out);
    out.extend_from_slice(&trailer);
    out
}

pub fn decode(bytes: &[u8]) -> Result<Manifest, FormatError> {
    if bytes.len() < HEADER_LEN + TRAILER_LEN {
        return Err(FormatError::TooShort {
            got: bytes.len(),
            need: HEADER_LEN + TRAILER_LEN,
        });
    }
    if &bytes[0..4] != MAGIC {
        return Err(FormatError::BadMagic);
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    if version != FORMAT_VERSION {
        return Err(FormatError::UnsupportedVersion(version));
    }
    let (body, trailer) = bytes.split_at(bytes.len() - TRAILER_LEN);
    if Sha256::digest(body).as_slice() != trailer {
        return Err(FormatError::TrailerMismatch);
    }

    let min_size = u32::from_le_bytes(bytes[6..10].try_into().unwrap()) as usize;
    let avg_bits = bytes[10] as u32;
    let max_size = u32::from_le_bytes(bytes[11..15].try_into().unwrap()) as usize;
    let total_len = u64::from_le_bytes(bytes[15..23].try_into().unwrap());
    let content_sha256 = hex::encode(&bytes[23..55]);
    let chunk_count = u32::from_le_bytes(bytes[55..59].try_into().unwrap()) as usize;

    let records_end = HEADER_LEN + chunk_count * CHUNK_RECORD_LEN;
    if bytes.len() < records_end + TRAILER_LEN {
        return Err(FormatError::TruncatedRecord { index: chunk_count.saturating_sub(1) });
    }
    if bytes.len() > records_end + TRAILER_LEN {
        return Err(FormatError::TrailingBytes(bytes.len() - records_end - TRAILER_LEN));
    }

    let mut chunks = Vec::with_capacity(chunk_count);
    for i in 0..chunk_count {
        let base = HEADER_LEN + i * CHUNK_RECORD_LEN;
        let offset = u64::from_le_bytes(bytes[base..base + 8].try_into().unwrap());
        let len = u32::from_le_bytes(bytes[base + 8..base + 12].try_into().unwrap()) as u64;
        let sha256 = hex::encode(&bytes[base + 12..base + 44]);
        chunks.push(ChunkMeta { offset, len, sha256 });
    }

    Ok(Manifest {
        algorithm: AlgorithmSpec {
            name: ALGORITHM_NAME.to_string(),
            version: ALGORITHM_VERSION,
            min_size,
            avg_bits,
            max_size,
            mask_hex: format!("0x{:016x}", (1u64 << avg_bits) - 1),
            chunk_digest: "sha256".to_string(),
        },
        total_len,
        content_sha256,
        chunks,
    })
}

fn decode_digest(hex_str: &str) -> Result<[u8; 32], FormatError> {
    let raw = hex::decode(hex_str).map_err(|_| FormatError::BadDigestHex)?;
    raw.try_into().map_err(|_| FormatError::BadDigestHex)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunker::chunk_all;
    use crate::params::ChunkParams;

    fn sample_manifest() -> Manifest {
        let params = ChunkParams { min_size: 64, avg_bits: 12, max_size: 1 << 14 };
        Manifest::from_outcome(&params, chunk_all(params, &vec![7u8; 100_000]))
    }

    #[test]
    fn roundtrip_preserves_manifest() {
        let m = sample_manifest();
        let bytes = encode(&m);
        let back = decode(&bytes).unwrap();
        assert_eq!(m, back);
    }

    #[test]
    fn roundtrip_empty_manifest() {
        let params = ChunkParams::default_params();
        let m = Manifest::empty(&params);
        assert_eq!(decode(&encode(&m)).unwrap(), m);
    }

    #[test]
    fn rejects_short_input() {
        assert_eq!(
            decode(b"CD"),
            Err(FormatError::TooShort { got: 2, need: HEADER_LEN + TRAILER_LEN })
        );
    }

    #[test]
    fn rejects_bad_magic() {
        let mut bytes = encode(&sample_manifest());
        bytes[0] = b'X';
        assert_eq!(decode(&bytes), Err(FormatError::BadMagic));
    }

    #[test]
    fn rejects_unknown_version() {
        let mut bytes = encode(&sample_manifest());
        bytes[4] = 99;
        assert_eq!(decode(&bytes), Err(FormatError::UnsupportedVersion(99)));
    }

    #[test]
    fn detects_single_bit_corruption_via_trailer() {
        let mut bytes = encode(&sample_manifest());
        let mid = bytes.len() / 2;
        bytes[mid] ^= 0x01;
        assert_eq!(decode(&bytes), Err(FormatError::TrailerMismatch));
    }

    #[test]
    fn detects_truncation() {
        let bytes = encode(&sample_manifest());
        let cut = bytes.len() - 10;
        assert!(matches!(decode(&bytes[..cut]), Err(FormatError::TrailerMismatch) | Err(FormatError::TooShort { .. })));
    }
}
