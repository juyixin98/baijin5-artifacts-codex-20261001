//! Regenerate the committed binary fixtures from `fixtures/keys_*.txt`
//! and `fixtures/params.json`.
//!
//! Run: `cargo run --example gen_fixtures`
//!
//! Note: only the *table bytes* come from the kernel (that is the format
//! contract being pinned). The expected difference sets come from
//! `scripts/gen_expected.sh` (sort/comm), not from this code.

use std::fs;
use std::path::Path;

use iblt_service::format;
use iblt_service::iblt::{Iblt, Params};

fn read_keys(path: &Path) -> Vec<u64> {
    fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
        .split_whitespace()
        .map(|s| s.parse::<u64>().expect("fixture keys must be u64"))
        .collect()
}

fn main() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    let params_text = fs::read_to_string(dir.join("params.json")).expect("read params.json");
    let params_json: serde_json::Value =
        serde_json::from_str(&params_text).expect("parse params.json");
    let params = Params {
        cells: params_json["cells"].as_u64().expect("cells") as u32,
        k: params_json["k"].as_u64().expect("k") as u32,
        seed: params_json["seed"].as_u64().expect("seed"),
    };

    for side in ["a", "b"] {
        let keys = read_keys(&dir.join(format!("keys_{side}.txt")));
        let mut table = Iblt::new(params).expect("valid fixture params");
        for &key in &keys {
            table.insert(key);
        }
        let out = dir.join(format!("table_{side}_v1.iblt"));
        fs::write(&out, format::serialize(&table)).expect("write fixture");
        println!("wrote {} ({} keys, {} cells)", out.display(), keys.len(), params.cells);
    }
}
