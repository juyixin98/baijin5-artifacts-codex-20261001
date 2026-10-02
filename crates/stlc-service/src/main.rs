//! Entry point with two modes:
//!
//! * `stlc-server serve [--config path]`         start the HTTP service
//! * `stlc-server oneshot --term '...' [--budget N] [--sig 'name : Type']...`
//!   run one request and print the JSON response, exiting non-zero on failure
//!
//! The oneshot mode makes verification possible without standing up a server.

use std::path::PathBuf;
use std::process::ExitCode;

use stlc_core::run_source;
use stlc_proof::Binding;
use stlc_service::config::Config;
use stlc_service::server;
use stlc_syntax::parse::parse_type;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("serve") => {
            let config_path = match args.get(1) {
                Some(flag) if flag == "--config" => args.get(2).map(PathBuf::from),
                Some(other) => {
                    eprintln!("unknown serve argument: {other}");
                    return ExitCode::from(2);
                }
                None => Some(PathBuf::from("config/service.json")),
            };
            let cfg = match Config::load(config_path.as_deref()) {
                Ok(cfg) => cfg,
                Err(e) => {
                    eprintln!("config error: {e}");
                    return ExitCode::from(2);
                }
            };
            if let Err(e) = server::serve(cfg) {
                eprintln!("server error: {e}");
                return ExitCode::FAILURE;
            }
            ExitCode::SUCCESS
        }
        Some("oneshot") => oneshot(&args[1..]),
        _ => {
            eprintln!("usage: stlc-server serve [--config FILE] | stlc-server oneshot --term EXPR [--budget N] [--sig 'n : T']...");
            ExitCode::from(2)
        }
    }
}

fn oneshot(args: &[String]) -> ExitCode {
    let mut term: Option<String> = None;
    let mut budget: Option<usize> = None;
    let mut sigs: Vec<String> = Vec::new();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--term" => {
                term = args.get(i + 1).cloned();
                i += 2;
            }
            "--budget" => {
                budget = args.get(i + 1).and_then(|v| v.parse().ok());
                i += 2;
            }
            "--sig" => {
                if let Some(s) = args.get(i + 1) {
                    sigs.push(s.clone());
                }
                i += 2;
            }
            other => {
                eprintln!("unknown oneshot argument: {other}");
                return ExitCode::from(2);
            }
        }
    }
    let Some(term) = term else {
        eprintln!("oneshot requires --term EXPR");
        return ExitCode::from(2);
    };
    let cfg = Config::default();
    let budget = budget.unwrap_or(cfg.budget);
    let mut signature = Vec::new();
    for line in sigs {
        let Some((name, ty_src)) = line.split_once(':') else {
            eprintln!("--sig entries must look like `name : Type`");
            return ExitCode::from(2);
        };
        match parse_type(ty_src.trim()) {
            Ok(ty) => signature.push(Binding {
                name: name.trim().to_string(),
                ty,
            }),
            Err(e) => {
                eprintln!("bad signature type: {e}");
                return ExitCode::from(2);
            }
        }
    }
    match run_source(&term, signature, budget) {
        Ok(resp) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&resp).expect("response serializes")
            );
            ExitCode::SUCCESS
        }
        Err(err) => {
            let payload = serde_json::json!({
                "ok": false,
                "category": match &err {
                    stlc_core::error::DriverError::Parse { .. } => "parse_error",
                    stlc_core::error::DriverError::Check(stlc_core::error::NormError::Type(_)) => "type_error",
                    stlc_core::error::DriverError::Check(stlc_core::error::NormError::BudgetExhausted { .. }) => "budget_exhausted",
                    stlc_core::error::DriverError::Check(stlc_core::error::NormError::Internal { .. }) => "internal",
                },
                "message": err.to_string(),
                "error": serde_json::to_value(&err).unwrap_or(serde_json::Value::Null),
            });
            println!("{}", serde_json::to_string_pretty(&payload).unwrap());
            ExitCode::FAILURE
        }
    }
}
