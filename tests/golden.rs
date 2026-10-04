//! Golden-file tests: committed fixtures + expected boundaries.
//!
//! The expected files were generated once and are independently reproducible
//! by `scripts/reference_chunker.py --check` (a Python implementation that
//! shares only the spec, not the Rust code). These tests therefore pin the
//! Rust core against answers it did not generate at test time.

use cdc_service::chunker::chunk_all;
use cdc_service::params::ChunkParams;
use serde::Deserialize;

#[derive(Deserialize)]
struct GoldenParams {
    min_size: usize,
    avg_bits: u32,
    max_size: usize,
}

#[derive(Deserialize)]
struct Golden {
    params: GoldenParams,
    total_len: u64,
    content_sha256: String,
    boundaries: Vec<u64>,
    chunk_lengths: Vec<u64>,
    chunk_sha256: Vec<String>,
}

fn check_fixture(name: &str) {
    let dir = env!("CARGO_MANIFEST_DIR");
    let data = std::fs::read(format!("{dir}/tests/fixtures/{name}.bin")).unwrap();
    let golden: Golden = serde_json::from_str(
        &std::fs::read_to_string(format!("{dir}/tests/fixtures/expected/{name}.json")).unwrap(),
    )
    .unwrap();

    let params = ChunkParams {
        min_size: golden.params.min_size,
        avg_bits: golden.params.avg_bits,
        max_size: golden.params.max_size,
    };
    let out = chunk_all(params, &data);

    assert_eq!(out.total_len, golden.total_len, "{name}: total_len");
    assert_eq!(out.content_sha256, golden.content_sha256, "{name}: content digest");
    let offsets: Vec<u64> = out.chunks.iter().map(|c| c.offset).collect();
    assert_eq!(offsets, golden.boundaries, "{name}: boundaries");
    let lens: Vec<u64> = out.chunks.iter().map(|c| c.len).collect();
    assert_eq!(lens, golden.chunk_lengths, "{name}: chunk lengths");
    let digests: Vec<&str> = out.chunks.iter().map(|c| c.sha256.as_str()).collect();
    assert_eq!(digests, golden.chunk_sha256, "{name}: chunk digests");
}

#[test]
fn golden_empty() {
    check_fixture("empty");
}

#[test]
fn golden_random_64k() {
    check_fixture("random_64k");
}

#[test]
fn golden_repeat_64k() {
    check_fixture("repeat_64k");
}

#[test]
fn golden_text_sample() {
    check_fixture("text_sample");
}
