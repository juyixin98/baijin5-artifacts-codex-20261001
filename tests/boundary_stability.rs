//! Boundary stability evidence.
//!
//! Two independent oracles are used:
//!  1. A NAIVE reference chunker (below, in this test file) that recomputes
//!     the window hash from scratch at every position — a different code
//!     path from the streaming core, so a state-carrying bug in the core
//!     cannot be mirrored here.
//!  2. Boundary comparisons between original and locally-modified inputs,
//!     reporting the exact byte range of chunks disturbed by an edit.

use cdc_service::chunker::{chunk_buffer, ChunkParams, ChunkRef, WINDOW_BYTES};
use cdc_service::gear::GEAR;

const PARAMS: ChunkParams = ChunkParams {
    min_size: 256,
    max_size: 4096,
    mask_bits: 10,
    pattern: 0,
};

/// Naive reference: for each position, recompute the hash as a fresh fold
/// over the bytes since the last cut (bounded by the window). O(n*w) —
/// deliberately simple and independent of the streaming implementation.
fn naive_boundaries(data: &[u8], params: &ChunkParams) -> Vec<usize> {
    let mask = params.mask();
    let mut boundaries = Vec::new();
    let mut start = 0usize;
    for pos in 0..data.len() {
        let len = pos - start + 1;
        if len < params.min_size {
            continue;
        }
        let window_start = if len > WINDOW_BYTES { pos + 1 - WINDOW_BYTES } else { start };
        let mut h: u64 = 0;
        for &b in &data[window_start..=pos] {
            h = h.wrapping_shl(1).wrapping_add(GEAR[b as usize]);
        }
        if len >= params.max_size || (h & mask) == params.pattern {
            boundaries.push(pos + 1);
            start = pos + 1;
        }
    }
    // Final flush, mirroring Chunker::finish: a trailing partial chunk is
    // emitted; empty input yields no boundaries at all.
    if start < data.len() {
        boundaries.push(data.len());
    }
    boundaries
}

fn ends(chunks: &[ChunkRef]) -> Vec<usize> {
    chunks.iter().map(|c| (c.offset + c.len as u64) as usize).collect()
}

/// Deterministic pseudo-random bytes (xorshift64*), no external crates.
fn prng_bytes(seed: u64, n: usize) -> Vec<u8> {
    let mut x = seed.max(1);
    (0..n)
        .map(|_| {
            x ^= x >> 12;
            x ^= x << 25;
            x ^= x >> 27;
            (x.wrapping_mul(0x2545F4914F6CDD1D) >> 56) as u8
        })
        .collect()
}

#[test]
fn streaming_core_matches_naive_reference_on_all_input_kinds() {
    let inputs: Vec<(&str, Vec<u8>)> = vec![
        ("empty", vec![]),
        ("tiny", b"x".to_vec()),
        ("random", prng_bytes(0xC0FFEE, 50_000)),
        ("long-repeat", vec![0x5Au8; 30_000]),
        ("mixed", {
            let mut v = prng_bytes(7, 10_000);
            v.extend(vec![0u8; 10_000]);
            v.extend(prng_bytes(8, 10_000));
            v
        }),
    ];
    for (name, data) in inputs {
        let got = ends(&chunk_buffer(PARAMS, &data).unwrap());
        let want = naive_boundaries(&data, &PARAMS);
        assert_eq!(got, want, "{name}: streaming core disagrees with naive reference");
    }
}

/// Locate the byte range disturbed by a local edit, in base coordinates:
/// the longest identical prefix of boundary lists, and the longest suffix
/// that aligns with a constant shift of `delta` (the edit's length change).
/// Returns (first_disturbed_byte, first_resynced_boundary) or None when the
/// maps are fully aligned.
fn changed_range(base_ends: &[usize], edited_ends: &[usize], delta: i64) -> Option<(usize, usize)> {
    let mut prefix = 0;
    while prefix < base_ends.len() && prefix < edited_ends.len() && base_ends[prefix] == edited_ends[prefix] {
        prefix += 1;
    }
    let mut suffix = 0;
    while suffix < base_ends.len() - prefix && suffix < edited_ends.len() - prefix {
        let b = base_ends[base_ends.len() - 1 - suffix];
        let e = edited_ends[edited_ends.len() - 1 - suffix];
        if e as i64 - b as i64 != delta {
            break;
        }
        suffix += 1;
    }
    if prefix >= base_ends.len() && prefix >= edited_ends.len() {
        return None; // boundary lists identical
    }
    if prefix + suffix > base_ends.len() {
        return None; // regions overlap: only possible when delta == 0, i.e. no change
    }
    let start = if prefix == 0 { 0 } else { base_ends[prefix - 1] };
    let end = if suffix == 0 {
        *base_ends.last().unwrap()
    } else {
        base_ends[base_ends.len() - suffix]
    };
    Some((start, end))
}

#[test]
fn small_insert_disturbs_only_a_local_chunk_range() {
    let base = prng_bytes(0xBACE, 60_000);
    let insert_at = 30_000usize;
    let insert: Vec<u8> = prng_bytes(0x1AB5E27, 100);
    let mut edited = base.clone();
    edited.splice(insert_at..insert_at, insert.iter().copied());

    let base_chunks = chunk_buffer(PARAMS, &base).unwrap();
    let edited_chunks = chunk_buffer(PARAMS, &edited).unwrap();

    // Prefix stability: every boundary strictly before the chunk containing
    // the edit must be identical.
    let base_ends = ends(&base_chunks);
    let edited_ends = ends(&edited_chunks);
    let edit_chunk_start = *base_ends.iter().rev().find(|&&e| e <= insert_at).unwrap_or(&0);
    let prefix_base: Vec<_> = base_ends.iter().copied().take_while(|&e| e <= edit_chunk_start).collect();
    let prefix_edited: Vec<_> = edited_ends.iter().copied().take(prefix_base.len()).collect();
    assert_eq!(prefix_base, prefix_edited, "boundaries before the edit moved");

    // Re-synchronization: because the hash resets at every cut, the first
    // content-defined cut after the edit region re-anchors the stream; from
    // there on, boundaries align with a constant shift of +insert.len().
    // changed_range computes exactly this prefix/suffix alignment.
    let delta = insert.len() as i64;
    let (first, resync_at) = changed_range(&base_ends, &edited_ends, delta)
        .expect("an insertion must change the chunk map");

    // Report the disturbed range (this is the evidence artifact).
    eprintln!(
        "insert of {} bytes at {} disturbed base range [{}, {}) — {} bytes of {} total \
         ({} -> {} chunks)",
        insert.len(), insert_at, first, resync_at, resync_at - first,
        base.len(), base_chunks.len(), edited_chunks.len()
    );
    // Locality: disturbance starts no earlier than one max_size before the
    // edit (the chunk containing the edit point) and re-synchronizes within
    // one max_size after it.
    assert!(first + PARAMS.max_size >= insert_at, "disturbance started too early: {first}");
    assert!(resync_at <= insert_at + PARAMS.max_size, "stream did not re-synchronize promptly: {resync_at}");
}

#[test]
fn long_repeat_is_cut_by_max_size_only_and_is_stable() {
    let data = vec![0x77u8; 100_000];
    let chunks = chunk_buffer(PARAMS, &data).unwrap();
    // Constant bytes pin the low hash bits after WINDOW_BYTES, so every cut
    // here must be the forced max_size cut (plus a short tail).
    for (i, c) in chunks.iter().enumerate() {
        if i + 1 < chunks.len() {
            assert_eq!(c.len as usize, PARAMS.max_size, "non-final chunk must be max_size");
        }
    }
    // Same content chunked in 1-byte feeds gives identical boundaries.
    let mut c = cdc_service::chunker::Chunker::new(PARAMS).unwrap();
    let mut got = Vec::new();
    for b in &data {
        got.extend(c.feed(std::slice::from_ref(b)));
    }
    got.extend(c.finish());
    assert_eq!(chunks, got);
}

#[test]
fn random_input_boundaries_are_content_defined_not_size_defined() {
    let data = prng_bytes(0x9A110D, 100_000);
    let chunks = chunk_buffer(PARAMS, &data).unwrap();
    assert!(chunks.len() > 10, "expected many chunks, got {}", chunks.len());
    let sizes: Vec<usize> = chunks.iter().map(|c| c.len as usize).collect();
    let all_max = sizes[..sizes.len() - 1].iter().all(|&s| s == PARAMS.max_size);
    assert!(!all_max, "random input must produce content-defined cuts, not only forced cuts");
    for (i, &s) in sizes.iter().enumerate() {
        assert!(s <= PARAMS.max_size, "chunk {i} exceeds max_size");
        if i + 1 < sizes.len() {
            assert!(s >= PARAMS.min_size, "non-final chunk {i} below min_size");
        }
    }
}
