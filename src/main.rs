//! CLI entry point: run one model-counting request end to end.
//!
//! Usage:
//!   dmc count --request <request.json> [--config <config.json>]
//!             [--proof <proof.json>]
//!
//! Exit codes: 0 = accepted, 1 = rejected/undecidable, 2 = usage or IO
//! error. The structured diagnostic is always printed to stdout as
//! JSON; sensitive request labels appear only in redacted form.

use dmc::config::Config;
use dmc::diag::Category;
use dmc::pipeline::run_request;
use dmc::request::Request;
use std::process::ExitCode;

struct Args {
    request: String,
    config: Option<String>,
    proof: Option<String>,
}

fn parse_args(argv: &[String]) -> Result<Args, String> {
    let mut it = argv.iter();
    match it.next().map(String::as_str) {
        Some("count") => {}
        _ => return Err("expected subcommand `count`".to_string()),
    }
    let mut request = None;
    let mut config = None;
    let mut proof = None;
    while let Some(flag) = it.next() {
        let value = it
            .next()
            .ok_or_else(|| format!("flag {flag} needs a value"))?;
        match flag.as_str() {
            "--request" => request = Some(value.clone()),
            "--config" => config = Some(value.clone()),
            "--proof" => proof = Some(value.clone()),
            other => return Err(format!("unknown flag {other}")),
        }
    }
    Ok(Args {
        request: request.ok_or_else(|| "missing --request <file>".to_string())?,
        config,
        proof,
    })
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = match parse_args(&argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("usage error: {e}");
            eprintln!("usage: dmc count --request <request.json> [--config <config.json>] [--proof <proof.json>]");
            return ExitCode::from(2);
        }
    };

    let config = match &args.config {
        Some(path) => match std::fs::read_to_string(path)
            .map_err(|e| e.to_string())
            .and_then(|s| Config::from_json_str(&s).map_err(|e| e.to_string()))
        {
            Ok(c) => c,
            Err(e) => {
                eprintln!("cannot load config {path}: {e}");
                return ExitCode::from(2);
            }
        },
        None => Config::default(),
    };

    let request_text = match std::fs::read_to_string(&args.request) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("cannot read request {}: {e}", args.request);
            return ExitCode::from(2);
        }
    };
    let request = match Request::from_json_str(&request_text) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("cannot parse request {}: {e}", args.request);
            return ExitCode::from(2);
        }
    };

    let result = run_request(&request, &config);
    println!("{}", result.diagnostic.to_json());

    if let (Some(path), Some(proof)) = (&args.proof, &result.proof) {
        if let Err(e) = std::fs::write(path, proof.to_json()) {
            eprintln!("cannot write proof {path}: {e}");
            return ExitCode::from(2);
        }
    }

    match result.diagnostic.category {
        Category::Accepted => {
            if let Some(count) = result.count {
                println!("count={count}");
            }
            ExitCode::SUCCESS
        }
        _ => ExitCode::from(1),
    }
}
