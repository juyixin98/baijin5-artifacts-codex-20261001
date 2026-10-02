//! mmu-lab 服务入口。
//!
//! 环境变量：
//! - `MMU_LISTEN`：监听地址，默认 `127.0.0.1:8080`
//! - `MMU_TOTAL_FRAMES`：物理帧池帧数，默认 65536（256 MiB）
//! - `MMU_TLB_CAPACITY`：TLB 容量，默认 16
//! - `MMU_MAX_ASIDS`：ASID 上界，默认 255
//! - `MMU_SNAPSHOT_PATH`：默认快照文件，默认 `./store/mmu-lab.json`
//! - `MMU_AUTOLOAD`：设为 `1` 时启动若快照存在则自动载入

use mmu_lab::api::{router, AppState};
use mmu_lab::config::Config;
use mmu_lab::store::Lab;
use std::env;

fn env_or<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn main() {
    let listen = env::var("MMU_LISTEN").unwrap_or_else(|_| "127.0.0.1:8080".into());
    let snapshot_path =
        env::var("MMU_SNAPSHOT_PATH").unwrap_or_else(|_| "./store/mmu-lab.json".into());

    let config = Config {
        total_frames: env_or("MMU_TOTAL_FRAMES", 1 << 16),
        tlb_capacity: env_or("MMU_TLB_CAPACITY", 16),
        max_asids: env_or("MMU_MAX_ASIDS", 255),
        event_buffer: env_or("MMU_EVENT_BUFFER", 2048),
    };
    config.validate().expect("非法机器资源配置");

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("构建 tokio runtime 失败");

    runtime.block_on(async move {
        let lab = if env::var("MMU_AUTOLOAD").as_deref() == Ok("1")
            && std::path::Path::new(&snapshot_path).exists()
        {
            match Lab::load_from_file(&snapshot_path) {
                Ok(l) => {
                    eprintln!("[mmu-lab] 已从 {snapshot_path} 自动载入快照");
                    l
                }
                Err(e) => {
                    eprintln!("[mmu-lab] 自动载入失败，改用空状态：{e}");
                    Lab::new(config.clone())
                }
            }
        } else {
            Lab::new(config.clone())
        };

        let state = AppState::new(lab, snapshot_path.clone());
        let app = router(state);

        let listener = tokio::net::TcpListener::bind(&listen)
            .await
            .unwrap_or_else(|e| panic!("绑定 {listen} 失败：{e}"));
        eprintln!("[mmu-lab] 受限多级页表/TLB 教学服务监听 http://{listen}");
        eprintln!("[mmu-lab] 合成环境：不触碰真实内核页表；快照路径 {snapshot_path}");

        axum::serve(listener, app)
            .with_graceful_shutdown(shutdown_signal())
            .await
            .expect("服务运行失败");
    });
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("安装 Ctrl-C 处理器失败");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("安装 SIGTERM 处理器失败")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {}
        _ = terminate => {}
    }
    eprintln!("[mmu-lab] 收到关闭信号，正在退出");
}
