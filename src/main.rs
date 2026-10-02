//! ARC 页替换引擎 HTTP 服务入口。
//!
//! 用法：
//! ```text
//! arc-page-cache --config config/arc.toml
//! arc-page-cache --config config/arc-zero.toml      # 容量为零（缓存关闭）
//! ```

use std::process::ExitCode;
use std::sync::{Arc, Mutex};

use arc_page_cache::api;
use arc_page_cache::config::AppConfig;
use arc_page_cache::engine::Engine;
use arc_page_cache::state::SampledState;
use arc_page_cache::storage::FilePageStore;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let config_path = parse_config_arg(&args).unwrap_or_else(|| "config/arc.toml".to_string());

    let cfg = match AppConfig::from_toml_path(std::path::Path::new(&config_path)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("fatal: {e}");
            return ExitCode::FAILURE;
        }
    };

    // 初始化文件后端（本地合成数据目录）。
    let store = match FilePageStore::new(&cfg.server.data_dir, cfg.cache.page_size) {
        Ok(s) => Box::new(s),
        Err(e) => {
            eprintln!(
                "fatal: cannot initialize data dir {}: {e}",
                cfg.server.data_dir
            );
            return ExitCode::FAILURE;
        }
    };

    let mut engine = Engine::new(cfg.cache.clone(), store, 1024);

    // 恢复采样状态（仅 p 与统计；列表冷启动，见 README）。
    let state_path = std::path::Path::new(&cfg.server.data_dir).join("state.json");
    match SampledState::load(&state_path) {
        Ok(Some(s)) if s.supported() => {
            engine.restore_adaptive_p(s.p);
            eprintln!("restored sampled state: p={} (c={})", s.p, s.c);
        }
        Ok(Some(_)) => eprintln!("warning: unsupported state version; cold start"),
        Ok(None) => eprintln!("no prior state; cold start"),
        Err(e) => eprintln!("warning: cannot load state ({e}); cold start"),
    }

    let engine = Arc::new(Mutex::new(engine));
    let app = api::router(engine.clone());

    // std 绑定可在 runtime 外完成；tokio 的 from_std 必须在 runtime 内。
    let std_listener = match std::net::TcpListener::bind(&cfg.server.bind) {
        Ok(l) => {
            l.set_nonblocking(true).expect("set nonblocking");
            l
        }
        Err(e) => {
            eprintln!("fatal: cannot bind {}: {e}", cfg.server.bind);
            return ExitCode::FAILURE;
        }
    };

    eprintln!(
        "ARC page-cache listening on http://{} (c={}, data_dir={})",
        cfg.server.bind, cfg.cache.capacity, cfg.server.data_dir
    );

    let rt = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("fatal: cannot build tokio runtime: {e}");
            return ExitCode::FAILURE;
        }
    };

    rt.block_on(async move {
        let listener = tokio::net::TcpListener::from_std(std_listener).expect("listener from std");
        let server = axum::serve(listener, app);
        // 优雅关停：Ctrl-C 时把 p/统计落盘。
        let shutdown = async {
            let _ = tokio::signal::ctrl_c().await;
            eprintln!("\nshutting down; persisting sampled state...");
        };
        if let Err(e) = server.with_graceful_shutdown(shutdown).await {
            eprintln!("server error: {e}");
        }

        // 关停前先回写全部脏页（通过明确的回写适配器），再持久化采样状态。
        if let Err(err) = engine.lock().unwrap().flush_dirty() {
            // 回写失败：仍保存采样状态，但明确提示有未落盘脏页。
            eprintln!("warning: dirty flush incomplete: {err}");
        }

        // 持久化采样状态（不含页内容）。
        let snapshot = {
            let e = engine.lock().unwrap();
            SampledState::new(e.config().capacity, e.cache().p(), e.stats())
        };
        let path = std::path::Path::new(&cfg.server.data_dir).join("state.json");
        if let Err(err) = snapshot.save(&path) {
            eprintln!("warning: failed to persist state: {err}");
        } else {
            eprintln!("sampled state saved to {}", path.display());
        }
    });

    ExitCode::SUCCESS
}

fn parse_config_arg(args: &[String]) -> Option<String> {
    let mut iter = args.iter();
    iter.next()?;
    match iter.next() {
        Some(a) if a == "--config" || a == "-c" => iter.next().cloned(),
        Some(a) if a.starts_with("--config=") => Some(a["--config=".len()..].to_string()),
        _ => None,
    }
}
