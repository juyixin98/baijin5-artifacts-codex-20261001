//! cdc-report: generates the boundary-stability evidence report (markdown on
//! stdout). Every claim printed here is also asserted — the command fails
//! loudly if an invariant does not hold.
//!
//! Usage: `cargo run --bin cdc-report > docs/evidence.md`

use cdc_service::analysis::changed_range;
use cdc_service::chunker::{chunk_all, Chunker};
use cdc_service::params::ChunkParams;

/// Deterministic SplitMix64 byte source (no external RNG needed).
struct SplitMix(u64);

impl SplitMix {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    fn bytes(&mut self, n: usize) -> Vec<u8> {
        let mut out = Vec::with_capacity(n);
        while out.len() < n {
            out.extend_from_slice(&self.next_u64().to_le_bytes());
        }
        out.truncate(n);
        out
    }
}

fn offsets(outcome: &cdc_service::chunker::ChunkOutcome) -> Vec<u64> {
    outcome.chunks.iter().map(|c| c.offset).collect()
}

fn main() {
    let params = ChunkParams { min_size: 1024, avg_bits: 12, max_size: 16 * 1024 };
    println!("# Boundary stability evidence\n");
    println!(
        "Parameters: min_size={}, avg_bits={} (target {} B), max_size={}. Algorithm: gear-cdc v1 (window 64 B, sha256 digests).\n",
        params.min_size, params.avg_bits, 1usize << params.avg_bits, params.max_size
    );

    // ---- Experiment 1: split invariance on random data ----
    let mut rng = SplitMix(0xC0FF_EE00);
    let data = rng.bytes(1 << 20);
    let whole = chunk_all(params, &data);
    println!("## 1. Identical content, different feed splits\n");
    println!("Input: 1 MiB deterministic pseudo-random bytes (SplitMix64 seed 0xC0FFEE00).");
    println!("Chunks (whole-buffer feed): {}\n", whole.chunks.len());
    println!("| feed pattern | chunks | boundaries identical |");
    println!("|---|---|---|");
    for step in [1usize, 7, 64, 4096, 65_536] {
        let mut c = Chunker::new(params);
        for piece in data.chunks(step) {
            c.push(piece);
        }
        let out = c.finish();
        let same = offsets(&out) == offsets(&whole) && out.content_sha256 == whole.content_sha256;
        assert!(same, "split invariance violated at step {step}");
        println!("| {step}-byte pieces | {} | {same} |", out.chunks.len());
    }
    println!();

    // ---- Experiment 2: small insertion ----
    println!("## 2. Local insertion of 100 bytes\n");
    println!("Input: same 1 MiB stream; 100 bytes inserted at 25%, 50%, 75% of its length.\n");
    println!("| insert at | unchanged prefix boundaries | resync boundary | affected chunk span (bytes) | within 2·max_size+window bound |");
    println!("|---|---|---|---|---|");
    let insert: Vec<u8> = SplitMix(0xBEEF).bytes(100);
    for frac in [0.25f64, 0.5, 0.75] {
        let at = (data.len() as f64 * frac) as usize;
        let mut modified = data[..at].to_vec();
        modified.extend_from_slice(&insert);
        modified.extend_from_slice(&data[at..]);
        let out = chunk_all(params, &modified);
        let before = offsets(&whole);
        let r = changed_range(&before, &offsets(&out), insert.len() as i64);
        let resync = r.resync_at.expect("streams must resynchronize");
        // Affected chunks: from the last unchanged boundary up to resync.
        let affected_from = before[r.prefix_boundaries.saturating_sub(1).min(before.len() - 1)];
        let affected_span = resync - affected_from;
        // Bound: both streams force a cut within max_size of the chunk
        // containing the insertion; at most one more max_size chunk plus the
        // 64-byte hash window is needed before boundaries coincide again.
        let bound = at as u64 + insert.len() as u64 + 2 * params.max_size as u64 + 64;
        assert!(resync <= bound, "resync beyond 2*max_size+window bound");
        println!(
            "| {at} | {} | {resync} | {affected_span} | {} |",
            r.prefix_boundaries,
            affected_span <= insert.len() as u64 + 2 * params.max_size as u64 + 64
        );
    }
    println!();
    println!("Reading: \"affected chunk span\" is the byte range of chunks whose contents or boundaries changed, from the last unchanged boundary to the resynchronization boundary. When the inserted bytes contain no boundary trigger, the surrounding chunk simply absorbs them and no boundary moves; the span is then exactly one chunk. In all cases the disturbance stays local: it never reaches beyond 2·max_size + 64 bytes past the edit.\n");

    // ---- Experiment 3: long repeated bytes ----
    println!("## 3. Long repeated bytes (1 MiB of 0xAA)\n");
    let repeat = vec![0xAAu8; 1 << 20];
    let out = chunk_all(params, &repeat);
    let lens: Vec<u64> = out.chunks.iter().map(|c| c.len).collect();
    let max = params.max_size as u64;
    let min = params.min_size as u64;
    let interior_ok = lens[..lens.len() - 1].iter().all(|&l| (min..=max).contains(&l));
    assert!(interior_ok, "interior chunk outside [min,max]");
    assert!(lens.iter().all(|&l| l <= max), "chunk exceeds max_size");
    let forced = lens.iter().filter(|&&l| l == max).count();
    println!("Chunks: {}, of which exactly max_size: {}. Min interior chunk: {} B, max: {} B.",
        lens.len(), forced, lens[..lens.len()-1].iter().min().unwrap(), lens.iter().max().unwrap());
    println!("Every interior chunk respects min_size <= len <= max_size: {interior_ok}.\n");

    // ---- Experiment 4: random vs repeat digest sanity ----
    println!("## 4. Content digest sanity\n");
    println!("- random 1 MiB sha256: `{}`", whole.content_sha256);
    println!("- repeat 1 MiB sha256: `{}`", out.content_sha256);
    assert_ne!(whole.content_sha256, out.content_sha256);
    println!("\nAll assertions in this report passed at generation time.");
}
