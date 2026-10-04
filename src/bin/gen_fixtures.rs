//! cdc-gen-fixtures: (re)generates the sample inputs and golden boundary
//! files under `tests/fixtures/`. The golden files are committed; the
//! integration tests validate the chunker against them, and
//! `scripts/reference_chunker.py` independently reproduces them.
//!
//! Usage: `cargo run --bin cdc-gen-fixtures`

use std::path::Path;

use cdc_service::chunker::chunk_all;
use cdc_service::params::ChunkParams;

/// Golden parameters: small on purpose so 64 KiB inputs yield many chunks.
fn golden_params() -> ChunkParams {
    ChunkParams { min_size: 256, avg_bits: 10, max_size: 4096 }
}

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

fn text_sample() -> Vec<u8> {
    let mut out = Vec::new();
    let base = "the quick brown fox jumps over the lazy dog. ";
    let mut n: u32 = 0;
    while out.len() < 64 * 1024 {
        out.extend_from_slice(base.as_bytes());
        out.extend_from_slice(format!("line {n}.\n").as_bytes());
        n += 1;
    }
    out.truncate(64 * 1024);
    out
}

fn main() {
    let dir = Path::new("tests/fixtures");
    let expected = dir.join("expected");
    std::fs::create_dir_all(&expected).unwrap();

    let cases: Vec<(&str, Vec<u8>)> = vec![
        ("empty", Vec::new()),
        ("random_64k", SplitMix(0xF17C_0001).bytes(64 * 1024)),
        ("repeat_64k", vec![0x5Au8; 64 * 1024]),
        ("text_sample", text_sample()),
    ];

    let params = golden_params();
    for (name, data) in &cases {
        std::fs::write(dir.join(format!("{name}.bin")), data).unwrap();
        let outcome = chunk_all(params, data);
        let golden = serde_json::json!({
            "params": {
                "min_size": params.min_size,
                "avg_bits": params.avg_bits,
                "max_size": params.max_size,
            },
            "total_len": outcome.total_len,
            "content_sha256": outcome.content_sha256,
            "boundaries": outcome.chunks.iter().map(|c| c.offset).collect::<Vec<_>>(),
            "chunk_lengths": outcome.chunks.iter().map(|c| c.len).collect::<Vec<_>>(),
            "chunk_sha256": outcome.chunks.iter().map(|c| c.sha256.clone()).collect::<Vec<_>>(),
        });
        std::fs::write(
            expected.join(format!("{name}.json")),
            serde_json::to_string_pretty(&golden).unwrap(),
        )
        .unwrap();
        println!("{name}: {} bytes -> {} chunks", data.len(), outcome.chunks.len());
    }
}
