//! psi-client: run one party of the PSI protocol against a psi-server.
//!
//! Element file format: one element per line (UTF-8). Empty lines are
//! skipped. Duplicates are collapsed to set semantics (a note is printed).

use std::io::{BufRead, BufReader};
use std::process::ExitCode;
use std::time::Duration;

use clap::{Parser, Subcommand};
use psi_dh::client::Client;

#[derive(Parser)]
#[command(name = "psi-client", about = "Two-party DH-PSI client (teaching grade)")]
struct Cli {
    /// Base URL of the psi-server.
    #[arg(long, default_value = "http://127.0.0.1:38711", global = true)]
    server: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Create a new session; prints the session id.
    NewSession,
    /// Run one party of the protocol.
    Run {
        /// "a" (learns the intersection) or "b" (learns nothing).
        #[arg(long)]
        role: String,
        /// Session id (hex) from `new-session`.
        #[arg(long)]
        session: String,
        /// Path to the element file (one element per line).
        #[arg(long)]
        file: String,
        /// Seconds to wait for the other party before giving up.
        #[arg(long, default_value_t = 120)]
        timeout_secs: u64,
    },
    /// Print the (redacted) audit log of a session.
    Audit {
        #[arg(long)]
        session: String,
    },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let client = Client::new(&cli.server);

    let result = match &cli.command {
        Commands::NewSession => match client.new_session() {
            Ok(id) => {
                println!("{id}");
                Ok(())
            }
            Err(e) => Err(e.to_string()),
        },
        Commands::Run {
            role,
            session,
            file,
            timeout_secs,
        } => {
            let client = client
                .with_polling(Duration::from_millis(200), Duration::from_secs(*timeout_secs));
            match load_elements(file) {
                Ok(elements) => match role.as_str() {
                    "a" => match client.run_party_a(session, &elements) {
                        Ok(intersection) => {
                            for element in &intersection {
                                println!("{}", String::from_utf8_lossy(element));
                            }
                            eprintln!(
                                "[psi-client] intersection size: {} (only party A learns this)",
                                intersection.len()
                            );
                            Ok(())
                        }
                        Err(e) => Err(e.to_string()),
                    },
                    "b" => match client.run_party_b(session, &elements) {
                        Ok(()) => {
                            eprintln!("[psi-client] party B done (learned nothing)");
                            Ok(())
                        }
                        Err(e) => Err(e.to_string()),
                    },
                    other => Err(format!("unknown role '{other}'; expected 'a' or 'b'")),
                },
                Err(e) => Err(e),
            }
        }
        Commands::Audit { session } => match client.audit(session) {
            Ok(records) => {
                for r in &records {
                    println!(
                        "#{} ts={} event={} outcome={} request_id={} detail={}",
                        r.id, r.ts_unix, r.event, r.outcome, r.request_id, r.detail
                    );
                }
                Ok(())
            }
            Err(e) => Err(e.to_string()),
        },
    };

    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(msg) => {
            eprintln!("[psi-client] error: {msg}");
            ExitCode::FAILURE
        }
    }
}

fn load_elements(path: &str) -> Result<Vec<Vec<u8>>, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("open {path}: {e}"))?;
    let mut elements = Vec::new();
    for line in BufReader::new(file).lines() {
        let line = line.map_err(|e| format!("read {path}: {e}"))?;
        if !line.is_empty() {
            elements.push(line.into_bytes());
        }
    }
    Ok(elements)
}
