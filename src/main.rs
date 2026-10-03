//! CLI entry point.
//!
//!   arc-cache serve [--config PATH]
//!   arc-cache replay --config PATH --trace FILE.jsonl
//!   arc-cache init-pages --dir DIR --pages N [--size BYTES]

use std::path::PathBuf;
use std::process::ExitCode;

use arc_cache::api;
use arc_cache::config::Config;
use arc_cache::diag::Op;
use arc_cache::engine::{Engine, Request};
use arc_cache::store::FilePageStore;
use arc_cache::writeback::FsWriteback;

const USAGE: &str = "arc-cache — ARC page replacement engine

USAGE:
  arc-cache serve      [--config PATH]     start the HTTP API (default config: arc-cache.toml)
  arc-cache replay     --config PATH --trace FILE
                                           replay a JSONL request trace offline
  arc-cache init-pages --dir DIR --pages N [--size BYTES]
                                           create deterministic local page fixtures

TRACE LINE FORMAT:
  {\"request_id\": \"r1\", \"op\": \"read\",  \"page\": 3}
  {\"request_id\": \"r2\", \"op\": \"write\", \"page\": 3, \"data_hex\": \"deadbeef\"}
";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(cmd) = args.first() else {
        eprint!("{USAGE}");
        return ExitCode::from(2);
    };
    let result = match cmd.as_str() {
        "serve" => cmd_serve(&args[1..]),
        "replay" => cmd_replay(&args[1..]),
        "init-pages" => cmd_init_pages(&args[1..]),
        _ => {
            eprint!("{USAGE}");
            return ExitCode::from(2);
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}

fn flag_value(args: &[String], name: &str) -> Option<String> {
    args.windows(2)
        .find(|w| w[0] == name)
        .map(|w| w[1].clone())
}

fn load_config(args: &[String]) -> Result<Config, String> {
    match flag_value(args, "--config") {
        Some(path) => Config::load(std::path::Path::new(&path)),
        None => Ok(Config::default()),
    }
}

fn build_engine(cfg: &Config) -> Engine {
    let store = FilePageStore::new(&cfg.page_store_dir);
    let wb = FsWriteback::new(store.clone());
    let mut engine = Engine::new(
        cfg.capacity,
        Box::new(store),
        Box::new(wb),
        cfg.diag_capacity,
        cfg.sample_interval,
        cfg.log_raw_keys,
    );
    engine.set_snapshot_path(cfg.snapshot_path.clone());
    engine
}

fn cmd_serve(args: &[String]) -> Result<(), String> {
    let cfg = load_config(args)?;
    let listen = cfg.listen.clone();
    let engine = build_engine(&cfg);
    let app = api::router(engine);

    let rt = tokio::runtime::Runtime::new().map_err(|e| e.to_string())?;
    rt.block_on(async move {
        let listener = tokio::net::TcpListener::bind(&listen)
            .await
            .map_err(|e| format!("bind {listen}: {e}"))?;
        eprintln!("arc-cache serving on http://{listen}");
        axum::serve(listener, app).await.map_err(|e| e.to_string())
    })
}

fn cmd_replay(args: &[String]) -> Result<(), String> {
    let cfg = load_config(args)?;
    let trace = flag_value(args, "--trace").ok_or("replay requires --trace FILE")?;
    let text = std::fs::read_to_string(&trace).map_err(|e| format!("read {trace}: {e}"))?;

    let mut engine = build_engine(&cfg);
    let mut total = 0usize;
    let mut failed = 0usize;

    for (lineno, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let req: TraceLine = serde_json::from_str(line)
            .map_err(|e| format!("{}:{}: {e}", trace, lineno + 1))?;
        let op = match req.op.as_str() {
            "read" => Op::Read,
            "write" => Op::Write,
            other => return Err(format!("{}:{}: unknown op '{other}'", trace, lineno + 1)),
        };
        let data = match &req.data_hex {
            Some(h) => Some(arc_cache::hexutil::decode(h)?),
            None => None,
        };
        let resp = engine.submit(Request {
            request_id: req.request_id,
            op,
            page: req.page,
            data,
        });
        total += 1;
        match &resp.result {
            Ok(ok) => println!(
                "seq={} id={} ok outcome={:?}",
                resp.seq, resp.request_id, ok.outcome
            ),
            Err(e) => {
                failed += 1;
                println!("seq={} id={} error={}", resp.seq, resp.request_id, e);
            }
        }
    }

    let stats = engine.stats();
    println!(
        "summary: total={total} failed={failed} hits={} ghost_hits={} misses={} \
         read_throughs={} write_throughs={} writebacks={} writeback_failures={}",
        stats.hits_t1 + stats.hits_t2,
        stats.ghost_hits_b1 + stats.ghost_hits_b2,
        stats.misses,
        stats.read_throughs,
        stats.write_throughs,
        stats.writebacks,
        stats.writeback_failures,
    );
    println!(
        "final: p={} lists={:?}",
        engine.cache().p(),
        engine.cache().lists()
    );
    Ok(())
}

#[derive(serde::Deserialize)]
struct TraceLine {
    request_id: Option<String>,
    op: String,
    page: u64,
    data_hex: Option<String>,
}

fn cmd_init_pages(args: &[String]) -> Result<(), String> {
    let dir = flag_value(args, "--dir").ok_or("init-pages requires --dir DIR")?;
    let pages: u64 = flag_value(args, "--pages")
        .ok_or("init-pages requires --pages N")?
        .parse()
        .map_err(|_| "--pages must be an integer")?;
    let size: usize = flag_value(args, "--size")
        .and_then(|s| s.parse().ok())
        .unwrap_or(256);

    let store = FilePageStore::new(PathBuf::from(&dir));
    for page in 0..pages {
        // Deterministic content: page id repeated, so fixtures are
        // reproducible and verifiable.
        let byte = (page % 251) as u8;
        store.store(page, &vec![byte; size])?;
    }
    println!("created {pages} pages of {size} bytes in {dir}");
    Ok(())
}
