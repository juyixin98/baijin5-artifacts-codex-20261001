//! Interop test: the streaming chunker (rolling state across arbitrary feed
//! splits) must agree with an independent, non-rolling reference
//! implementation on every input class.

mod common;

use cdc_service::chunker::{chunk_all, Chunker};
use cdc_service::params::ChunkParams;
use common::{reference_boundaries, SplitMix};
use sha2::Digest;

fn params() -> ChunkParams {
    ChunkParams { min_size: 256, avg_bits: 10, max_size: 4096 }
}

fn assert_matches_reference(data: &[u8], label: &str) {
    let p = params();
    let expected = reference_boundaries(data, p.min_size, p.avg_bits, p.max_size);

    // Whole-buffer feed.
    let whole = chunk_all(p, data);
    let got: Vec<u64> = whole.chunks.iter().map(|c| c.offset).collect();
    assert_eq!(got, expected, "{label}: whole-buffer boundaries differ from reference");

    // Awkward feeds: 1 byte, 7 bytes, 64 bytes (window size), 1000 bytes.
    for step in [1usize, 7, 64, 1000] {
        let mut c = Chunker::new(p);
        for piece in data.chunks(step) {
            c.push(piece);
        }
        let out = c.finish();
        let got: Vec<u64> = out.chunks.iter().map(|c| c.offset).collect();
        assert_eq!(got, expected, "{label}: {step}-byte feed boundaries differ from reference");
    }

    // Per-chunk digests recomputed independently from the raw bytes.
    let mut start = 0usize;
    for (i, &_b) in expected.iter().enumerate() {
        let end = if i + 1 < expected.len() { expected[i + 1] as usize } else { data.len() };
        let digest = hex::encode(sha2::Sha256::digest(&data[start..end]));
        assert_eq!(whole.chunks[i].sha256, digest, "{label}: chunk {i} digest");
        assert_eq!(whole.chunks[i].offset as usize, start);
        assert_eq!(whole.chunks[i].len as usize, end - start);
        start = end;
    }
}

#[test]
fn random_data_matches_reference() {
    let data = SplitMix(0xDEAD_0001).bytes(200_000);
    assert_matches_reference(&data, "random");
}

#[test]
fn repeated_bytes_match_reference() {
    let data = vec![0x00u8; 150_000];
    assert_matches_reference(&data, "zeros");
    let data = vec![0xFFu8; 150_000];
    assert_matches_reference(&data, "ones");
}

#[test]
fn structured_text_matches_reference() {
    let mut data = Vec::new();
    for i in 0..5000u32 {
        data.extend_from_slice(format!("record {i}: value={}\n", i.wrapping_mul(7919)).as_bytes());
    }
    assert_matches_reference(&data, "text");
}

#[test]
fn edge_inputs_match_reference() {
    assert_matches_reference(b"", "empty");
    assert_matches_reference(b"x", "one byte");
    assert_matches_reference(&vec![7u8; 255], "below min_size");
    assert_matches_reference(&vec![7u8; 256], "exactly min_size");
    assert_matches_reference(&vec![9u8; 4096], "exactly max_size");
    assert_matches_reference(&vec![9u8; 4097], "max_size plus one");
}

#[test]
fn insertion_shifts_only_local_boundaries() {
    // Cross-check the reference on a modified stream too, not just originals.
    let mut data = SplitMix(0x1234_5678).bytes(100_000);
    let insert = SplitMix(0x9999).bytes(333);
    let at = 40_000usize;
    let mut modified = data[..at].to_vec();
    modified.extend_from_slice(&insert);
    modified.extend_from_slice(&data[at..]);
    assert_matches_reference(&modified, "inserted");
    // And the original is still consistent after we touched a copy.
    data.shrink_to_fit();
    assert_matches_reference(&data, "original");
}
