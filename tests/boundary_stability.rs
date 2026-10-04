//! Boundary stability: local modifications must only affect a bounded,
//! reportable range of chunks. Mirrors `cdc-report` as hard assertions.

mod common;

use cdc_service::analysis::changed_range;
use cdc_service::chunker::chunk_all;
use cdc_service::params::ChunkParams;
use common::SplitMix;

fn params() -> ChunkParams {
    ChunkParams { min_size: 1024, avg_bits: 12, max_size: 16 * 1024 }
}

fn offsets(data: &[u8]) -> Vec<u64> {
    chunk_all(params(), data).chunks.iter().map(|c| c.offset).collect()
}

#[test]
fn small_insertion_changes_bounded_range() {
    let data = SplitMix(0xC0FF_EE00).bytes(1 << 20);
    let insert = SplitMix(0xBEEF).bytes(100);
    let before = offsets(&data);
    let max = params().max_size as u64;

    for frac in [0.25f64, 0.5, 0.75] {
        let at = (data.len() as f64 * frac) as usize;
        let mut modified = data[..at].to_vec();
        modified.extend_from_slice(&insert);
        modified.extend_from_slice(&data[at..]);
        let after = offsets(&modified);

        let r = changed_range(&before, &after, insert.len() as i64);
        // Every boundary at or before the insertion point is preserved, in
        // order (the prefix may extend further by coincidence, which is fine).
        let must_survive = before.iter().filter(|&&b| b as usize <= at).count();
        assert!(
            r.prefix_boundaries >= must_survive,
            "insert at {at}: only {} of {must_survive} pre-insertion boundaries survived",
            r.prefix_boundaries
        );
        // The streams must resynchronize. Bound: both streams force a cut
        // within max_size of the chunk containing the insertion; at most one
        // more max_size chunk (plus the 64-byte hash window) is needed before
        // a boundary coincides again.
        let resync = r.resync_at.unwrap_or_else(|| panic!("insert at {at}: no resync"));
        let bound = at as u64 + insert.len() as u64 + 2 * max + 64;
        assert!(resync <= bound, "insert at {at}: resync {resync} beyond bound {bound}");
        // The affected byte range is local: strictly smaller than the whole input.
        let span = r.span().unwrap();
        assert!(
            span <= insert.len() as u64 + 2 * max + 64,
            "insert at {at}: span {span} too large"
        );
        // Suffix boundaries reappear shifted by exactly the insert length.
        assert!(r.suffix_boundaries >= 1, "insert at {at}: suffix must realign");
    }
}

#[test]
fn long_repeat_forces_max_size_cuts() {
    let data = vec![0xAAu8; 1 << 20];
    let out = chunk_all(params(), &data);
    let max = params().max_size as u64;
    let min = params().min_size as u64;
    assert!(out.chunks.len() > 10, "expected many chunks, got {}", out.chunks.len());
    for (i, c) in out.chunks.iter().enumerate() {
        assert!(c.len <= max, "chunk {i} exceeds max_size");
        if i + 1 < out.chunks.len() {
            assert!(c.len >= min, "interior chunk {i} below min_size");
        }
    }
    // Degenerate input: the overwhelming majority of cuts must be forced.
    let forced = out.chunks.iter().filter(|c| c.len == max).count();
    assert!(
        forced as f64 > 0.9 * out.chunks.len() as f64,
        "only {forced}/{} chunks are max_size",
        out.chunks.len()
    );
}

#[test]
fn random_data_boundaries_are_split_invariant_and_stable() {
    let data = SplitMix(0xABBA_2024).bytes(512 * 1024);
    let whole = offsets(&data);
    // Same content, chunked differently at the I/O level: identical result.
    let mut c = cdc_service::chunker::Chunker::new(params());
    for piece in data.chunks(13) {
        c.push(piece);
    }
    let out = c.finish();
    let streamed: Vec<u64> = out.chunks.iter().map(|c| c.offset).collect();
    assert_eq!(whole, streamed);

    // Average chunk size is in the expected band for random data.
    let avg = data.len() as f64 / whole.len() as f64;
    let target = (1usize << params().avg_bits) as f64;
    assert!(
        (target * 0.4..target * 2.5).contains(&avg),
        "average chunk size {avg} outside expected band around {target}"
    );
}

#[test]
fn deletion_changes_bounded_range() {
    let data = SplitMix(0xD313_7000).bytes(1 << 20);
    let before = offsets(&data);
    let at = 600_000usize;
    let del = 2000usize;
    let mut modified = data[..at].to_vec();
    modified.extend_from_slice(&data[at + del..]);
    let after = offsets(&modified);

    let r = changed_range(&before, &after, -(del as i64));
    let resync = r.resync_at.expect("streams must resynchronize after deletion");
    assert!(resync <= at as u64 + 2 * params().max_size as u64 + 64);
    assert!(r.span().unwrap() <= 2 * params().max_size as u64 + del as u64 + 64);
}
