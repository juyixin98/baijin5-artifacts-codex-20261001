//! Integration tests for the binary manifest format and the recovery kernel,
//! exercising them together as the encode/store/recover path.

mod common;

use cdc_service::chunker::chunk_all;
use cdc_service::format::{self, FormatError};
use cdc_service::manifest::Manifest;
use cdc_service::params::ChunkParams;
use cdc_service::recovery::{self, VerifyError};
use common::SplitMix;
use sha2::Digest;

fn params() -> ChunkParams {
    ChunkParams { min_size: 256, avg_bits: 10, max_size: 4096 }
}

fn sample() -> (Vec<u8>, Manifest) {
    let data = SplitMix(0xABCD_0001).bytes(80_000);
    let m = Manifest::from_outcome(&params(), chunk_all(params(), &data));
    (data, m)
}

#[test]
fn encode_store_decode_recover_roundtrip() {
    let (data, manifest) = sample();
    // "Store" the manifest in binary form and recover it.
    let stored = format::encode(&manifest);
    let recovered_manifest = format::decode(&stored).unwrap();
    assert_eq!(recovered_manifest, manifest);
    // Reassemble the original bytes and verify byte-for-byte.
    let bytes = recovery::reassemble(&recovered_manifest, &data).unwrap();
    assert_eq!(bytes, data);
    recovery::verify_against(&recovered_manifest, &data, &data).unwrap();
}

#[test]
fn corrupted_stored_manifest_is_rejected_with_category() {
    let (_data, manifest) = sample();
    let mut stored = format::encode(&manifest);
    // Flip one bit inside a chunk record.
    let mid = stored.len() / 2;
    stored[mid] ^= 0x01;
    let err = format::decode(&stored).unwrap_err();
    assert_eq!(err, FormatError::TrailerMismatch);
}

#[test]
fn tampered_chunk_byte_is_localized_by_recovery() {
    let (data, manifest) = sample();
    let mut bad = data.clone();
    let target = 2; // third chunk
    let at = manifest.chunks[target].offset as usize + 5;
    bad[at] ^= 0x80;
    let err = recovery::reassemble(&manifest, &bad).unwrap_err();
    assert_eq!(
        err,
        VerifyError::ChunkDigestMismatch { index: target, offset: manifest.chunks[target].offset }
    );
    assert_eq!(err.category(), "chunk_digest_mismatch");
}

#[test]
fn byte_compare_is_authoritative_over_digest() {
    // Digest checks pass on a forged manifest; only the byte comparison
    // against the reference catches the forgery.
    let (data, mut manifest) = sample();
    let mut stored = data.clone();
    let c0 = manifest.chunks[0].clone();
    let at = c0.offset as usize;
    stored[at] ^= 0x01;
    let forged = sha2::Sha256::digest(&stored[at..at + c0.len as usize]);
    manifest.chunks[0].sha256 = hex::encode(forged);

    assert!(recovery::reassemble(&manifest, &stored).is_ok(), "digest hints accept the forgery");
    let err = recovery::verify_against(&manifest, &stored, &data).unwrap_err();
    assert_eq!(err, VerifyError::ByteMismatch { offset: at as u64 });
    assert_eq!(err.category(), "byte_mismatch");
}

#[test]
fn empty_manifest_roundtrips_through_binary_format() {
    let m = Manifest::empty(&params());
    let stored = format::encode(&m);
    let back = format::decode(&stored).unwrap();
    assert_eq!(back, m);
    recovery::verify_against(&back, b"", b"").unwrap();
}
