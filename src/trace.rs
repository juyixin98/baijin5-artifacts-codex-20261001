//! 本地合成访问轨迹。
//!
//! 所有“外部参与者”都由这些确定性生成器扮演，无需真实业务数据：
//! - [`scan_trace`]：一次性扫描大量页（扫描污染），用于检验 T1 不污染 T2；
//! - [`hotspot_switch_trace`]：热点集合 A→B 切换，检验 p 的自适应方向；
//! - [`mixed_trace`]：可复现的固定混合轨迹（独立模型逐步对照用）；
//! - JSONL 读写：每行一个 [`Access`]，与 `examples/gen-fixtures` 对应。

use std::io::{BufRead, Write};
use std::path::Path;

use crate::types::{Access, AccessKind, PageId};

/// 扫描污染轨迹：顺序读 `start..start+n` 各一次。
///
/// 对 ARC 的预期效果：扫描页只进入 T1（单次访问），
/// 随扫描推进从 T1 LRU 端被挤入 B1，不会晋升 T2，热点得以保留在 T2。
pub fn scan_trace(start: u64, n: u64) -> Vec<Access> {
    (start..start + n).map(Access::read).collect()
}

/// 热点切换轨迹。
///
/// 1. 对热点集合 A（`a_pages`）做 `rounds_a` 轮均匀重复访问（建立 T2）；
/// 2. 再对热点集合 B（`b_pages`）做 `rounds_b` 轮；
/// 3. 可选在末尾重新触碰 A，检验 B1 幽灵命中与 p 的增大方向。
pub fn hotspot_switch_trace(
    a_pages: &[u64],
    b_pages: &[u64],
    rounds_a: u32,
    rounds_b: u32,
    revisit_a: bool,
) -> Vec<Access> {
    let mut trace = Vec::new();
    for r in 0..rounds_a {
        // 偶数轮正向、奇数轮反向，避免访问顺序成为隐含假设。
        let mut round: Vec<u64> = a_pages.to_vec();
        if r % 2 == 1 {
            round.reverse();
        }
        trace.extend(round.iter().map(|p| Access::read(*p)));
    }
    for r in 0..rounds_b {
        let mut round: Vec<u64> = b_pages.to_vec();
        if r % 2 == 1 {
            round.reverse();
        }
        trace.extend(round.iter().map(|p| Access::read(*p)));
    }
    if revisit_a {
        trace.extend(a_pages.iter().map(|p| Access::read(*p)));
    }
    trace
}

/// 固定混合轨迹（手写、短小、期望值可手算），供独立模型逐步对照。
pub fn mixed_trace() -> Vec<Access> {
    use AccessKind::*;
    let spec: &[(u64, AccessKind)] = &[
        (0, Read),
        (1, Read),
        (2, Read),
        (3, Read),
        (0, Read), // 0: T1→T2
        (1, Read), // 1: T1→T2
        (4, Read), // 扫描式新页
        (5, Read),
        (0, Read),  // T2 命中
        (6, Write), // 脏页进入 T1
        (7, Write),
        (6, Read), // 6: T1→T2（脏）
        (8, Read),
        (9, Read),
        (2, Read), // 2 很可能已在 B1 → 幽灵命中
    ];
    spec.iter()
        .map(|(p, k)| Access {
            page: PageId(*p),
            kind: *k,
        })
        .collect()
}

/// 写轨迹为 JSONL：`{"page":3,"kind":"read"}`。
pub fn write_jsonl(path: &Path, trace: &[Access]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut f = std::fs::File::create(path)?;
    for a in trace {
        let line = serde_json::to_string(a)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        writeln!(f, "{line}")?;
    }
    Ok(())
}

/// 从 JSONL 读回轨迹；错误带行号，便于夹具排错。
pub fn read_jsonl(path: &Path) -> std::io::Result<Vec<Access>> {
    let f = std::fs::File::open(path)?;
    let reader = std::io::BufReader::new(f);
    let mut out = Vec::new();
    for (i, line) in reader.lines().enumerate() {
        let line = line?;
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let access: Access = serde_json::from_str(trimmed).map_err(|e| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("{}:{}: {e}", path.display(), i + 1),
            )
        })?;
        out.push(access);
    }
    Ok(out)
}
