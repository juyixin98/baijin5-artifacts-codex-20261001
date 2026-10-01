//! Command line interface:
//!   fologic check --model m.json --request r.json
//!   fologic check-file request.json        (model_path/formula_path or inline)
//!   fologic verify --model m.json --proof p.json
//!   fologic serve [--addr 127.0.0.1:8140]   (line-delimited JSON over TCP)

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::ExitCode;

use fologic::checker::check;
use fologic::error::QeError;
use fologic::model::Model;
use fologic::proof::ProofRecord;
use fologic::service::{run_batch, run_check, BatchRequest, ErrorResponse};

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("check") => cmd_check(&args[1..]),
        Some("check-file") => cmd_check_file(&args[1..]),
        Some("verify") => cmd_verify(&args[1..]),
        Some("serve") => cmd_serve(&args[1..]),
        Some("--help") | Some("-h") | None => {
            print_help();
            return ExitCode::SUCCESS;
        }
        Some(other) => Err(QeError::input(
            "unknown_command",
            format!("unknown subcommand {}; see --help", other),
        )),
    };

    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            let envelope = serde_json::json!({
                "error": err,
            });
            eprintln!(
                "{}",
                serde_json::to_string_pretty(&envelope).unwrap_or_default()
            );
            ExitCode::from(match err.kind {
                fologic::error::ErrorKind::Input => 2,
                fologic::error::ErrorKind::StateConflict => 3,
                fologic::error::ErrorKind::ResourceExhausted => 4,
                fologic::error::ErrorKind::ComputationFailed => 5,
            })
        }
    }
}

fn print_help() {
    println!(
        "fologic - finite-domain first-order quantifier elimination\n\n\
USAGE:\n  \
fologic check --model MODEL.json --request REQUEST.json\n  \
fologic check-file BATCH.json\n  \
fologic verify --model MODEL.json --proof PROOF.json\n  \
fologic serve [--addr 127.0.0.1:8140]\n\n\
REQUEST FIELDS: formula (required), budget, node_cap, allow_empty_domain, run_id, verify\n\n\
EXIT CODES: 0 ok, 2 input, 3 state conflict, 4 resource exhausted, 5 computation failed"
    );
}

fn opt_value(args: &[String], name: &str) -> Option<String> {
    let mut iter = args.iter();
    while let Some(a) = iter.next() {
        if a == name {
            return iter.next().cloned();
        }
    }
    None
}

fn read_json<T: serde::de::DeserializeOwned>(path: &std::path::Path) -> Result<T, QeError> {
    let bytes = std::fs::read(path)
        .map_err(|e| QeError::input("fixture_unreadable", format!("{}: {}", path.display(), e)))?;
    serde_json::from_slice(&bytes)
        .map_err(|e| QeError::input("fixture_invalid_json", format!("{}: {}", path.display(), e)))
}

fn cmd_check(args: &[String]) -> Result<(), QeError> {
    let model_path = PathBuf::from(
        opt_value(args, "--model")
            .ok_or_else(|| QeError::input("missing_arg", "--model required"))?,
    );
    let request_path = PathBuf::from(
        opt_value(args, "--request")
            .ok_or_else(|| QeError::input("missing_arg", "--request required"))?,
    );
    let model: Model = read_json(&model_path)?;
    let mut request: fologic::service::CheckRequest = read_json(&request_path)?;
    let formula_path = opt_value(args, "--formula");
    if let Some(path) = formula_path {
        request.formula = read_json(&PathBuf::from(path))?;
    }
    let response = run_check(&model, request)?;
    println!("{}", serde_json::to_string_pretty(&response).unwrap());
    Ok(())
}

fn cmd_check_file(args: &[String]) -> Result<(), QeError> {
    let path = args
        .first()
        .map(PathBuf::from)
        .ok_or_else(|| QeError::input("missing_arg", "check-file needs BATCH.json"))?;
    let batch: BatchRequest = read_json(&path)?;
    let response = run_batch(batch)?;
    println!("{}", serde_json::to_string_pretty(&response).unwrap());
    Ok(())
}

fn cmd_verify(args: &[String]) -> Result<(), QeError> {
    let model_path = PathBuf::from(
        opt_value(args, "--model")
            .ok_or_else(|| QeError::input("missing_arg", "--model required"))?,
    );
    let proof_path = PathBuf::from(
        opt_value(args, "--proof")
            .ok_or_else(|| QeError::input("missing_arg", "--proof required"))?,
    );
    let model: Model = read_json(&model_path)?;
    let proof: ProofRecord = read_json(&proof_path)?;
    let report = check(&model, &proof)?;
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
    Ok(())
}

fn cmd_serve(args: &[String]) -> Result<(), QeError> {
    let addr = opt_value(args, "--addr").unwrap_or_else(|| "127.0.0.1:8140".to_string());
    let listener = TcpListener::bind(&addr)
        .map_err(|e| QeError::computation("bind_failed", format!("cannot bind {}: {}", addr, e)))?;
    eprintln!(
        "fologic serve listening on {} (line-delimited JSON BatchRequest)",
        addr
    );
    for stream in listener.incoming() {
        let stream = match stream {
            Ok(s) => s,
            Err(e) => {
                eprintln!("accept failed: {}", e);
                continue;
            }
        };
        let peer = stream.peer_addr().ok();
        let writer = stream
            .try_clone()
            .map_err(|e| QeError::computation("io_error", format!("clone stream: {}", e)))?;
        let mut writer = writer;
        let reader = BufReader::new(stream);
        for line in reader.lines() {
            let line = match line {
                Ok(l) => l,
                Err(e) => {
                    eprintln!("read from {:?}: {}", peer, e);
                    break;
                }
            };
            if line.trim().is_empty() {
                continue;
            }
            let answer = handle_line(&line);
            let _ = writeln!(writer, "{}", answer);
        }
    }
    Ok(())
}

fn handle_line(line: &str) -> String {
    let parsed: Result<BatchRequest, _> = serde_json::from_str(line);
    match parsed {
        Ok(batch) => match run_batch(batch) {
            Ok(response) => serde_json::to_string(&response).unwrap(),
            Err(err) => {
                let run_id = extract_run_id(line).unwrap_or_else(|| "unknown".to_string());
                serde_json::to_string(&ErrorResponse { run_id, error: err }).unwrap()
            }
        },
        Err(e) => serde_json::to_string(&ErrorResponse {
            run_id: "unknown".to_string(),
            error: QeError::input("fixture_invalid_json", e.to_string()),
        })
        .unwrap(),
    }
}

fn extract_run_id(line: &str) -> Option<String> {
    #[derive(serde::Deserialize)]
    struct IdOnly {
        #[serde(default)]
        run_id: Option<String>,
    }
    serde_json::from_str::<IdOnly>(line)
        .ok()
        .and_then(|v| v.run_id)
}
