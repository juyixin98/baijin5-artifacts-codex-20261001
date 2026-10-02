//! 生成本地合成轨迹夹具（JSONL）。
//!
//! ```text
//! cargo run --example gen-fixtures -- fixtures/
//! ```
//!
//! 产出：
//! - `scan.jsonl`：扫描污染轨迹
//! - `hotspot.jsonl`：热点 A→B 切换轨迹
//! - `mixed.jsonl`：固定手写混合轨迹（逐步对照用）

use std::path::PathBuf;

use arc_page_cache::trace::{hotspot_switch_trace, mixed_trace, scan_trace, write_jsonl};

fn main() {
    let dir = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("fixtures"));

    let scan = scan_trace(0, 32);
    let hotspot = hotspot_switch_trace(&[0, 1, 2, 3], &[100, 101, 102, 103], 4, 4, true);
    let mixed = mixed_trace();

    write_jsonl(&dir.join("scan.jsonl"), &scan).expect("write scan");
    write_jsonl(&dir.join("hotspot.jsonl"), &hotspot).expect("write hotspot");
    write_jsonl(&dir.join("mixed.jsonl"), &mixed).expect("write mixed");

    println!(
        "wrote fixtures to {}: scan={}, hotspot={}, mixed={} accesses",
        dir.display(),
        scan.len(),
        hotspot.len(),
        mixed.len()
    );
}
