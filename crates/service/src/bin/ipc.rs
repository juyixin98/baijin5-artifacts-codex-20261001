//! Command line entry point for the interpolation workbench.
//!
//! Subcommands:
//! - `interpolate --file request.json [--config config.json]`
//! - `serve [--config config.json]`
//! - `demo` (runs a built-in request, no files required)

use ipc_service::{handle_interpolate, InterpolateRequest, ServiceConfig};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        print_usage();
        std::process::exit(2);
    }
    match args[1].as_str() {
        "interpolate" => run_interpolate(&args[2..]),
        "serve" => run_serve(&args[2..]),
        "demo" => run_demo(),
        "--help" | "-h" | "help" => print_usage(),
        other => {
            eprintln!("unknown subcommand: {other}");
            print_usage();
            std::process::exit(2);
        }
    }
}

fn print_usage() {
    eprintln!(
        "ipc - propositional Craig interpolation\n\n\
         USAGE:\n  \
         ipc interpolate --file <request.json> [--config <config.json>]\n  \
         ipc serve [--config <config.json>]\n  \
         ipc demo\n"
    );
}

fn parse_flags(args: &[String]) -> (Option<PathBuf>, Option<PathBuf>) {
    let mut file = None;
    let mut config = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--file" | "-f" => {
                index += 1;
                file = args.get(index).map(PathBuf::from);
            }
            "--config" | "-c" => {
                index += 1;
                config = args.get(index).map(PathBuf::from);
            }
            other => eprintln!("ignoring argument: {other}"),
        }
        index += 1;
    }
    (file, config)
}

fn load_config(path: Option<&PathBuf>) -> ServiceConfig {
    ServiceConfig::load_or_default(path.map(|path| path.as_ref())).unwrap_or_else(|error| {
        eprintln!("warning: {error}; using built-in defaults");
        ServiceConfig::default()
    })
}

fn run_interpolate(args: &[String]) {
    let (file, config_path) = parse_flags(args);
    let path = file.unwrap_or_else(|| {
        eprintln!("missing --file <request.json>");
        std::process::exit(2);
    });
    let config = load_config(config_path.as_ref());
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| fatal(&format!("cannot read {}: {error}", path.display())));
    let request: InterpolateRequest = serde_json::from_str(&text)
        .unwrap_or_else(|error| fatal(&format!("invalid request JSON: {error}")));
    let response = handle_interpolate(request, &config);
    println!(
        "{}",
        serde_json::to_string_pretty(&response).expect("response serializes")
    );
}

fn run_serve(args: &[String]) {
    let (_, config_path) = parse_flags(args);
    let config = load_config(config_path.as_ref());
    ipc_service::server::serve(config).unwrap_or_else(|error| fatal(&error.to_string()));
}

fn run_demo() {
    let config = ServiceConfig::default();
    let request = InterpolateRequest {
        request_id: "demo-001".to_string(),
        antecedent: "a & (!a | d)".to_string(),
        consequent: "!d".to_string(),
        budget: Some(100),
        include_proof: Some(true),
    };
    let response = handle_interpolate(request, &config);
    println!(
        "{}",
        serde_json::to_string_pretty(&response).expect("response serializes")
    );
}

fn fatal(message: &str) -> ! {
    eprintln!("error: {message}");
    std::process::exit(1);
}
