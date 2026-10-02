//! Orchestration: parse -> prove -> independently verify -> respond.

use crate::api::{
    EngineIdentity, InterpolateRequest, InterpolateStatus, Metrics, ProofSummary,
    RejectionCategory, ServiceResponse, TraceEntry, VerificationSummary,
};
use crate::config::{ENGINE_NAME, ENGINE_VERSION, ServiceConfig};
use ipc_checker::verify_full;
use ipc_core::{
    EngineInput, EngineVerdict, InterpolationEngine, ResolutionBudget, UnknownReason,
};
use ipc_proof::ProofNode;
use ipc_syntax::parse;
use std::time::Instant;

fn engine_identity(config: &ServiceConfig) -> EngineIdentity {
    EngineIdentity {
        name: ENGINE_NAME.to_string(),
        version: ENGINE_VERSION.to_string(),
        site: config.site.clone(),
        modules: vec![
            "ipc-syntax".to_string(),
            "ipc-proof".to_string(),
            "ipc-core".to_string(),
            "ipc-checker".to_string(),
        ],
    }
}

fn trace_from_engine(log: &ipc_core::EventLog) -> Vec<TraceEntry> {
    log.events
        .iter()
        .map(|event| TraceEntry {
            step: event.step,
            module: event.module.clone(),
            level: format!("{:?}", event.level).to_lowercase(),
            message: event.message.clone(),
        })
        .collect()
}

/// Execute one interpolation request end to end.
pub fn handle_interpolate(
    request: InterpolateRequest,
    config: &ServiceConfig,
) -> ServiceResponse {
    let started = Instant::now();
    let mut parse_errors = Vec::new();
    let antecedent = match parse(&request.antecedent) {
        Ok(formula) => Some(formula),
        Err(error) => {
            parse_errors.push(format!("antecedent: {error}"));
            None
        }
    };
    let consequent = match parse(&request.consequent) {
        Ok(formula) => Some(formula),
        Err(error) => {
            parse_errors.push(format!("consequent: {error}"));
            None
        }
    };

    if !parse_errors.is_empty() {
        return response(
            request.request_id,
            config,
            Vec::new(),
            InterpolateStatus::InvalidInput {
                errors: parse_errors,
            },
            started,
        );
    }
    let a = antecedent.unwrap();
    let b = consequent.unwrap();

    let budget = request
        .budget
        .map(ResolutionBudget::capped)
        .unwrap_or_else(|| {
            config
                .default_budget
                .map(ResolutionBudget::capped)
                .unwrap_or_else(ResolutionBudget::unlimited)
        });

    let mut engine = InterpolationEngine::new(request.request_id.clone());
    engine
        .log
        .record("service", "request accepted; entering proving pipeline");
    let verdict = engine.run(EngineInput {
        a: &a,
        b: &b,
        budget,
    });

    let include_proof = request.include_proof.unwrap_or(config.include_proof);
    let status = match verdict {
        EngineVerdict::Proved(proved) => {
            engine.log.record(
                "checker",
                "running independent structural and truth-table verification",
            );
            let report = verify_full(&a, &b, &proved.interpolant, &proved.proof);
            let failures: Vec<serde_json::Value> = report
                .failures
                .iter()
                .map(serde_json::to_value)
                .filter_map(Result::ok)
                .collect();
            let verification = VerificationSummary {
                accepted: report.accepted(),
                failures: failures.clone(),
            };
            let proof_summary = if include_proof {
                Some(summarize_proof(&proved.proof))
            } else {
                None
            };
            if report.accepted() {
                InterpolateStatus::Proved {
                    interpolant: proved.interpolant.to_pretty(),
                    common_variables: report.common_variables,
                    verification,
                    proof: proof_summary,
                    metrics: Metrics {
                        resolutions_used: proved.resolutions_used,
                        variables_eliminated: proved.variables_eliminated,
                        elapsed_micros: started.elapsed().as_micros(),
                    },
                }
            } else {
                InterpolateStatus::Rejected {
                    category: RejectionCategory::VerificationFailed,
                    detail: serde_json::json!({
                        "candidate": proved.interpolant.to_pretty(),
                        "failures": failures,
                    }),
                }
            }
        }
        EngineVerdict::JointlySatisfiable(witness) => {
            engine
                .log
                .warn("service", "input pair is jointly satisfiable; no interpolant exists");
            InterpolateStatus::Rejected {
                category: RejectionCategory::JointlySatisfiable,
                detail: serde_json::to_value(&witness.assignment).unwrap_or(serde_json::json!({})),
            }
        }
        EngineVerdict::Unknown(reason) => match reason {
            UnknownReason::BudgetExhausted { used, cap } => InterpolateStatus::Unknown {
                reason: "budget_exhausted".to_string(),
                detail: serde_json::json!({
                    "resolutions_used": used,
                    "budget_cap": cap,
                    "note": "satisfiability was not decided; no proven interpolant is emitted",
                }),
            },
        },
    };

    let trace = trace_from_engine(&engine.log);
    if config.log_to_stderr {
        for entry in &trace {
            eprintln!(
                "[{}] step#{} {}: {}",
                request.request_id, entry.step, entry.module, entry.message
            );
        }
    }
    response(request.request_id, config, trace, status, started)
}

fn summarize_proof(proof: &ipc_proof::Proof) -> ProofSummary {
    let mut hypothesis_count = 0;
    let mut resolution_count = 0;
    for node in &proof.nodes {
        match node {
            ProofNode::Hypothesis { .. } => hypothesis_count += 1,
            ProofNode::Resolve { .. } => resolution_count += 1,
        }
    }
    ProofSummary {
        node_count: proof.nodes.len(),
        root: proof.root,
        hypothesis_count,
        resolution_count,
    }
}

fn response(
    request_id: String,
    config: &ServiceConfig,
    trace: Vec<TraceEntry>,
    status: InterpolateStatus,
    started: Instant,
) -> ServiceResponse {
    let _ = started;
    ServiceResponse {
        request_id,
        engine: engine_identity(config),
        status,
        trace,
    }
}
