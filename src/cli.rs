//! Command-line interface: local server, scenario runner, independent
//! verification and trace replay.

use std::net::SocketAddr;
use std::path::PathBuf;

use clap::{Parser, Subcommand};
use serde_json::json;

use crate::api::{router, AppState};
use crate::batch::multiset;
use crate::dto::JoinRequest;
use crate::engine;
use crate::error::{ErrorCategory, JoinError};
use crate::fixtures;
use crate::operator::reference;
use crate::resource::Budget;
use crate::state::SessionStore;
use crate::trace::{self, Outcome, Tracer};

#[derive(Parser, Debug)]
#[command(name = "iejoin", version, about = "IEJoin inequality-join backend")]
pub struct Cli {
    /// Directory for replayable run traces (default: none).
    #[arg(long, global = true)]
    pub trace_dir: Option<PathBuf>,

    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// Start the HTTP server.
    Serve {
        #[arg(long, default_value = "127.0.0.1:8080")]
        addr: SocketAddr,
    },
    /// Run one named synthetic scenario through IEJoin and the
    /// independent nested-loop oracle and compare multisets.
    Run {
        #[arg(long)]
        scenario: String,
        /// Emit machine-readable JSON instead of the text report.
        #[arg(long)]
        json: bool,
    },
    /// Cross-check every registered scenario against the oracle.
    Verify,
    /// Execute a raw JSON join request from a file.
    Request {
        #[arg(long)]
        file: PathBuf,
    },
    /// Replay a trace file against a scenario or request file and
    /// assert the recorded outcome/counters are reproduced.
    Replay {
        trace: PathBuf,
        /// Scenario name when the trace came from `run --scenario`.
        #[arg(long)]
        scenario: Option<String>,
        /// Request file when the trace came from `request --file`.
        #[arg(long)]
        request: Option<PathBuf>,
    },
}

/// CLI entry point. Returns the process exit code.
///
/// # Errors
/// Only through [`std::process::ExitCode`] (printed categorized error).
pub fn run(cli: Cli) -> std::process::ExitCode {
    let tracer = Tracer::new(cli.trace_dir.clone());
    match cli.command {
        Command::Serve { addr } => match serve(addr) {
            Ok(()) => std::process::ExitCode::SUCCESS,
            Err(e) => fail(&e),
        },
        Command::Run { scenario, json } => run_scenario(&tracer, &scenario, json),
        Command::Verify => verify_all(&tracer),
        Command::Request { file } => run_request_file(&tracer, &file),
        Command::Replay {
            trace: trace_path,
            scenario,
            request,
        } => replay(
            &tracer,
            &trace_path,
            scenario.as_deref(),
            request.as_deref(),
        ),
    }
}

fn tokio_runtime() -> Result<tokio::runtime::Runtime, std::io::Error> {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
}

fn serve(addr: SocketAddr) -> Result<(), JoinError> {
    let rt = tokio_runtime().map_err(|e| {
        JoinError::compute("runtime_init_failed", format!("cannot start tokio: {e}"))
    })?;
    rt.block_on(async move {
        let state = AppState::new(Tracer::new(None), SessionStore::new());
        let app = router(state);
        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .map_err(|e| JoinError::compute("bind_failed", format!("cannot bind {addr}: {e}")))?;
        tracing::info!(%addr, "IEJoin server listening");
        axum::serve(listener, app)
            .await
            .map_err(|e| JoinError::compute("server_error", format!("server failed: {e}")))
    })
}

fn pair_strings(
    pairs: &[crate::operator::iejoin::MatchPair],
    sc: &fixtures::Scenario,
) -> Vec<(String, String)> {
    pairs
        .iter()
        .map(|p| {
            (
                sc.left.row_ids[p.left_row as usize].clone(),
                sc.right.row_ids[p.right_row as usize].clone(),
            )
        })
        .collect()
}

/// Run one scenario: IEJoin vs nested loop; returns an exit code.
fn run_scenario(tracer: &Tracer, name: &str, as_json: bool) -> std::process::ExitCode {
    let sc = match fixtures::by_name(name) {
        Ok(sc) => sc,
        Err(e) => return fail(&e),
    };

    let result = verify_one(tracer, &sc);
    match result {
        Ok(report) => {
            if as_json {
                println!("{}", serde_json::to_string_pretty(&report).unwrap());
            } else {
                print_report(sc.name, &report);
            }
            std::process::ExitCode::SUCCESS
        }
        Err(e) => fail(&e),
    }
}

/// Result of cross-checking one scenario.
#[derive(serde::Serialize)]
struct ScenarioReport {
    scenario: String,
    iejoin_pairs: usize,
    reference_pairs: usize,
    multiset_match: bool,
    iejoin_candidate_accesses: u64,
    iejoin_gate_steps: u64,
    reference_p1_evaluations: u64,
    reference_p2_evaluations: u64,
    pairs: Vec<(String, String)>,
    run_id: String,
}

fn verify_one(tracer: &Tracer, sc: &fixtures::Scenario) -> Result<ScenarioReport, JoinError> {
    // Route IEJoin through the same engine the HTTP API uses, so a
    // replayable run trace is persisted when --trace-dir is set.
    let request = crate::dto::JoinRequest {
        left: fixtures::batch_to_dto(&sc.left),
        right: fixtures::batch_to_dto(&sc.right),
        predicates: predicates_to_dto(&sc.plan),
        budget: Some(Budget::default()),
        include_trace: true,
    };
    let parsed = request.parse()?;
    let out = engine::execute_oneshot(parsed, tracer)?;
    let run_id = out.run_id.clone();

    // Independent oracle, separate code path.
    let reference = reference::execute(&sc.plan, &sc.left, &sc.right)?;
    let ie_ms = multiset_from_pairs(&out.pairs, sc);
    let ref_ms = multiset(
        &reference
            .pairs
            .iter()
            .map(|p| crate::batch::OutputPair {
                left_id: p.left_id.clone(),
                right_id: p.right_id.clone(),
            })
            .collect::<Vec<_>>(),
    );

    if ie_ms != ref_ms {
        let e = JoinError::compute(
            "multiset_mismatch",
            format!(
                "scenario {}: IEJoin produced {} distinct pairs but reference produced {}",
                sc.name,
                ie_ms.len(),
                ref_ms.len()
            ),
        )
        .with("run_id", json!(run_id))
        .with("iejoin", json!(ie_ms))
        .with("reference", json!(ref_ms));
        return Err(e);
    }

    let mut pairs = pair_strings(&out.pairs, sc);
    pairs.sort();

    Ok(ScenarioReport {
        scenario: sc.name.to_owned(),
        iejoin_pairs: out.pairs.len(),
        reference_pairs: reference.pairs.len(),
        multiset_match: true,
        iejoin_candidate_accesses: out.counters.candidate_accesses,
        iejoin_gate_steps: out.counters.gate1_steps,
        reference_p1_evaluations: reference.stats.p1_evaluations,
        reference_p2_evaluations: reference.stats.p2_evaluations,
        pairs,
        run_id,
    })
}

fn multiset_from_pairs(
    pairs: &[crate::operator::iejoin::MatchPair],
    sc: &fixtures::Scenario,
) -> std::collections::BTreeMap<(String, String), usize> {
    let v: Vec<_> = pairs
        .iter()
        .map(|p| crate::batch::OutputPair {
            left_id: sc.left.row_ids[p.left_row as usize].clone(),
            right_id: sc.right.row_ids[p.right_row as usize].clone(),
        })
        .collect();
    multiset(&v)
}

fn print_report(name: &str, r: &ScenarioReport) {
    println!("scenario {name}: multiset_match={}", r.multiset_match);
    println!(
        "  pairs: iejoin={} reference={}",
        r.iejoin_pairs, r.reference_pairs
    );
    println!(
        "  work:  iejoin candidate_accesses={:<4} gate_steps={:<4} | nested-loop p1_evals={} p2_evals={}",
        r.iejoin_candidate_accesses,
        r.iejoin_gate_steps,
        r.reference_p1_evaluations,
        r.reference_p2_evaluations
    );
    print!("  result:");
    for (l, rr) in &r.pairs {
        print!(" ({l},{rr})");
    }
    println!();
    println!("  run_id: {}", r.run_id);
}

fn verify_all(tracer: &Tracer) -> std::process::ExitCode {
    let mut failures = 0usize;
    for name in fixtures::all_named() {
        match fixtures::by_name(name).and_then(|sc| verify_one(tracer, &sc)) {
            Ok(r) => print_report(name, &r),
            Err(e) => {
                failures += 1;
                eprintln!("FAIL {name}: [{}/{}] {}", e.category, e.code, e.message);
            }
        }
    }
    if failures == 0 {
        println!("all scenarios verified against nested-loop reference");
        std::process::ExitCode::SUCCESS
    } else {
        eprintln!("{failures} scenario(s) failed");
        std::process::ExitCode::FAILURE
    }
}

fn run_request_file(tracer: &Tracer, path: &std::path::Path) -> std::process::ExitCode {
    let bytes = match std::fs::read(path) {
        Ok(b) => b,
        Err(e) => {
            return fail(&JoinError::input(
                "request_file_unreadable",
                format!("cannot read {}: {e}", path.display()),
            ))
        }
    };
    let req: JoinRequest = match serde_json::from_slice(&bytes) {
        Ok(r) => r,
        Err(e) => {
            return fail(&JoinError::input(
                "invalid_request_json",
                format!("malformed request file: {e}"),
            ))
        }
    };
    let parsed = match req.parse() {
        Ok(p) => p,
        Err(e) => return fail(&e),
    };
    match engine::execute_oneshot(parsed, tracer) {
        Ok(out) => {
            let resp = json!({
                "run_id": out.run_id,
                "count": out.pairs.len(),
                "truncated": out.truncated,
                "counters": out.counters,
                "pairs": out.pair_dtos,
                "events": out.events,
            });
            println!("{}", serde_json::to_string_pretty(&resp).unwrap());
            std::process::ExitCode::SUCCESS
        }
        Err(e) => fail(&e),
    }
}

fn replay(
    tracer: &Tracer,
    trace_path: &std::path::Path,
    scenario: Option<&str>,
    request: Option<&std::path::Path>,
) -> std::process::ExitCode {
    let record = match trace::load(trace_path) {
        Ok(r) => r,
        Err(e) => return fail(&e),
    };

    // Rebuild exactly the same inputs locally.
    let parsed = if let Some(name) = scenario {
        match fixtures::by_name(name) {
            Ok(sc) => {
                let req = JoinRequest {
                    left: fixtures::batch_to_dto(&sc.left),
                    right: fixtures::batch_to_dto(&sc.right),
                    predicates: predicates_to_dto(&sc.plan),
                    budget: Some(record.budget.clone()),
                    include_trace: true,
                };
                match req.parse() {
                    Ok(p) => p,
                    Err(e) => return fail(&e),
                }
            }
            Err(e) => return fail(&e),
        }
    } else if let Some(path) = request {
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) => {
                return fail(&JoinError::input(
                    "request_file_unreadable",
                    format!("cannot read {}: {e}", path.display()),
                ))
            }
        };
        let req: JoinRequest = match serde_json::from_slice(&bytes) {
            Ok(r) => r,
            Err(e) => return fail(&JoinError::input("invalid_request_json", format!("{e}"))),
        };
        match req.parse() {
            Ok(p) => p,
            Err(e) => return fail(&e),
        }
    } else {
        return fail(&JoinError::input(
            "replay_input_missing",
            "replay requires --scenario NAME or --request FILE",
        ));
    };

    let rerun = match engine::execute_oneshot(parsed, tracer) {
        Ok(o) => o,
        Err(e) => return fail(&e),
    };

    let reproduced_outcome = match record.outcome {
        Outcome::Completed => !rerun.truncated,
        Outcome::Truncated => rerun.truncated,
        Outcome::Failed => false, // a recorded failure should not recur on valid replay
    };
    let reproduced_counts = record.counters.candidate_accesses == rerun.counters.candidate_accesses
        && record.pairs_emitted == rerun.pairs.len() as u64;

    if reproduced_outcome && reproduced_counts {
        println!(
            "REPRODUCED {} (run {} -> run {})",
            record.run_id,
            match record.outcome {
                Outcome::Completed => "completed",
                Outcome::Truncated => "truncated",
                Outcome::Failed => "failed",
            },
            rerun.run_id
        );
        std::process::ExitCode::SUCCESS
    } else {
        let e = JoinError::compute(
            "replay_diverged",
            format!(
                "replay of {} did not reproduce recorded result",
                record.run_id
            ),
        )
        .with("recorded_outcome", json!(record.outcome))
        .with(
            "reproduced_outcome",
            json!(if rerun.truncated {
                "truncated"
            } else {
                "completed"
            }),
        )
        .with("recorded_pairs", json!(record.pairs_emitted))
        .with("replayed_pairs", json!(rerun.pairs.len()));
        fail(&e)
    }
}

fn predicates_to_dto(plan: &crate::operator::JoinPlan) -> Vec<crate::dto::PredicateDto> {
    plan.predicates
        .iter()
        .map(|p| crate::dto::PredicateDto {
            left_column: p.left_column.clone(),
            op: match p.op {
                crate::operator::Comparator::Lt => "<".to_owned(),
                crate::operator::Comparator::Le => "<=".to_owned(),
                crate::operator::Comparator::Gt => ">".to_owned(),
                crate::operator::Comparator::Ge => ">=".to_owned(),
            },
            right_column: p.right_column.clone(),
        })
        .collect()
}

fn fail(e: &JoinError) -> std::process::ExitCode {
    let code = match e.category {
        ErrorCategory::Input => 2,
        ErrorCategory::StateConflict => 3,
        ErrorCategory::ResourceExhausted => 4,
        ErrorCategory::Compute => 5,
    };
    eprintln!("[{}/{}] {}", e.category, e.code, e.message);
    if !e.details.is_empty() {
        eprintln!("{}", serde_json::to_string_pretty(&e.details).unwrap());
    }
    std::process::ExitCode::from(code)
}
