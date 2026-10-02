//! cfs-sim CLI:
//!   cfs-sim scenarios                          list compiled-in scenarios
//!   cfs-sim run --scenario NAME [--data DIR]   run a builtin scenario
//!   cfs-sim run --fixture FILE [--data DIR]    run a scenario from JSON
//!   cfs-sim serve [--addr A] [--data DIR]      start the diagnostics API
//!
//! `run` exits 0 when every check passes, 1 otherwise.

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;

use cfs_sim::diag::{self, AppState};
use cfs_sim::persist::RunStore;
use cfs_sim::scenario::{builtin_scenarios, find_builtin, Scenario};
use cfs_sim::VERSION;

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(cmd) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    match cmd.as_str() {
        "scenarios" => {
            for s in builtin_scenarios() {
                println!(
                    "{:20} duration={}ms tasks={}  {}",
                    s.name,
                    s.duration_ms,
                    s.tasks.len(),
                    s.description
                );
            }
            ExitCode::SUCCESS
        }
        "run" => cmd_run(&args[1..]),
        "serve" => cmd_serve(&args[1..]),
        _ => {
            usage();
            ExitCode::from(2)
        }
    }
}

fn usage() {
    eprintln!(
        "cfs-sim {VERSION}\n\
         usage:\n\
         \tcfs-sim scenarios\n\
         \tcfs-sim run --scenario NAME | --fixture FILE [--data DIR]\n\
         \tcfs-sim serve [--addr 127.0.0.1:8080] [--data DIR]"
    );
}

fn flag_value<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.windows(2)
        .find(|w| w[0] == name)
        .map(|w| w[1].as_str())
}

fn cmd_run(args: &[String]) -> ExitCode {
    let data_dir = flag_value(args, "--data")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("data"));
    let scenario = if let Some(name) = flag_value(args, "--scenario") {
        match find_builtin(name) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("error: {e}");
                return ExitCode::from(2);
            }
        }
    } else if let Some(path) = flag_value(args, "--fixture") {
        let text = match std::fs::read_to_string(path) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("error: cannot read fixture {path}: {e}");
                return ExitCode::from(2);
            }
        };
        match Scenario::from_json(&text) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("error: invalid fixture {path}: {e}");
                return ExitCode::from(2);
            }
        }
    } else {
        eprintln!("error: run requires --scenario NAME or --fixture FILE");
        return ExitCode::from(2);
    };

    let store = match RunStore::new(&data_dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("error: cannot open data dir {}: {e}", data_dir.display());
            return ExitCode::from(2);
        }
    };

    match diag::execute_scenario(&store, &scenario) {
        Ok((run_id, report)) => {
            print_report(&run_id, &report);
            if report.passed {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            }
        }
        Err(e) => {
            eprintln!("run failed: {e}");
            ExitCode::FAILURE
        }
    }
}

fn print_report(run_id: &str, report: &cfs_sim::metrics::RunReport) {
    println!("run_id    : {run_id}");
    println!("version   : {}", report.version);
    println!("scenario  : {}", report.scenario);
    println!("status    : {:?}", report.status);
    println!(
        "time      : elapsed={}ms busy={}ms idle={}ms",
        report.elapsed_ms, report.busy_ms, report.idle_ms
    );
    println!(
        "{:14} {:>6} {:>7} {:>7} {:>7} {:>9} {:>9} {:>9}",
        "task", "weight", "exec_ms", "wait_ms", "sleep_ms", "max_wait", "exp_share", "act_share"
    );
    for t in &report.tasks {
        println!(
            "{:14} {:>6} {:>7} {:>7} {:>7} {:>9} {:>9} {:>9}",
            t.name,
            t.weight,
            t.exec_ms,
            t.wait_ms,
            t.sleep_ms,
            t.max_wait_ms,
            t.expected_share
                .map(|s| format!("{s:.4}"))
                .unwrap_or_else(|| "-".into()),
            t.actual_share
                .map(|s| format!("{s:.4}"))
                .unwrap_or_else(|| "-".into()),
        );
    }
    println!("checks:");
    for c in &report.checks {
        println!(
            "  [{}] {:28} {} (basis: {})",
            if c.passed { "PASS" } else { "FAIL" },
            c.name,
            c.detail,
            c.basis
        );
    }
    println!(
        "verdict   : {}",
        if report.passed { "PASS" } else { "FAIL" }
    );
}

fn cmd_serve(args: &[String]) -> ExitCode {
    let addr = flag_value(args, "--addr").unwrap_or("127.0.0.1:8080");
    let data_dir = flag_value(args, "--data")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("data"));
    let store = match RunStore::new(&data_dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("error: cannot open data dir {}: {e}", data_dir.display());
            return ExitCode::from(2);
        }
    };
    let state = Arc::new(AppState { store });
    let app = diag::router(state);

    let runtime = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("error: cannot start tokio runtime: {e}");
            return ExitCode::FAILURE;
        }
    };
    runtime.block_on(async move {
        let listener = match tokio::net::TcpListener::bind(addr).await {
            Ok(l) => l,
            Err(e) => {
                eprintln!("error: cannot bind {addr}: {e}");
                std::process::exit(2);
            }
        };
        tracing::info!(%addr, version = VERSION, "cfs-sim diagnostics API listening");
        if let Err(e) = axum::serve(listener, app).await {
            eprintln!("server error: {e}");
            std::process::exit(1);
        }
    });
    ExitCode::SUCCESS
}
