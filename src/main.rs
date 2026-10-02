//! mmu-teach 服务入口。
//!
//! 本地启动（无任何生产账号/真实数据依赖）：
//! ```bash
//! cargo run --release
//! MMU_LISTEN=127.0.0.1:8080 MMU_FRAMES=8192 MMU_RUNLOG=./runs.jsonl cargo run
//! ```

use mmu_teach::{api, Machine, RunLog};
use std::path::PathBuf;

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let listen = std::env::var("MMU_LISTEN").unwrap_or_else(|_| "127.0.0.1:8080".into());
    let frames: u64 = std::env::var("MMU_FRAMES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(mmu_teach::config::DEFAULT_FRAME_POOL);
    let sink: Option<PathBuf> = std::env::var("MMU_RUNLOG").ok().map(PathBuf::from);

    let log = match &sink {
        Some(path) => RunLog::with_jsonl_sink(path)?,
        None => RunLog::new(),
    };
    let machine = Machine::new(frames, log);

    println!("mmu-teach 启动");
    println!("  监听:       http://{listen}");
    println!("  物理帧池:   {frames} 帧（每帧 4KiB）");
    match &sink {
        Some(p) => println!("  运行日志:   {}", p.display()),
        None => println!("  运行日志:   仅内存环形缓冲（设置 MMU_RUNLOG=path 落盘 JSONL）"),
    }
    println!("  安全声明:   纯软件模拟，不读写任何真实内核页表");

    api::serve(&listen, api::AppState::new(machine)).await
}
