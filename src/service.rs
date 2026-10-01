//! Service boundary: request/response contracts and the full pipeline.
//!
//! Inputs may arrive inline or as local fixture paths. Every response (success
//! or structured failure) is serializable so CLI and tests share one contract.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::checker::{check, CheckReport};
use crate::error::{QeError, QeResult};
use crate::evaluator::evaluate_expanded;
use crate::expand::eliminate;
use crate::model::Model;
use crate::proof::{new_run_id, now_nanos, ProofRecord, RunLogger, VecLogger, Verdict};
use crate::syntax::Formula;

/// Default per-run guard for the size of the expanded AST.
pub const DEFAULT_NODE_CAP: u64 = 1_000_000;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckRequest {
    /// Caller supplied run id; generated when absent or empty.
    #[serde(default)]
    pub run_id: Option<String>,
    #[serde(default)]
    pub model_name: Option<String>,
    #[serde(default)]
    pub formula: Formula,
    /// Instantiation budget; `None` means unlimited.
    #[serde(default)]
    pub budget: Option<u64>,
    #[serde(default = "default_node_cap")]
    pub node_cap: u64,
    #[serde(default)]
    pub allow_empty_domain: bool,
    /// Run the independent checker before responding (default true).
    #[serde(default = "default_true")]
    pub verify: bool,
}

fn default_node_cap() -> u64 {
    DEFAULT_NODE_CAP
}
fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckResponse {
    pub run_id: String,
    pub verdict: Verdict,
    pub reason: String,
    pub instantiations_used: u64,
    pub instantiation_budget: u64,
    pub residual_quantifiers: usize,
    pub expanded_formula: Formula,
    pub proof: ProofRecord,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub check_report: Option<CheckReport>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorResponse {
    pub run_id: String,
    pub error: QeError,
}

/// Envelope used by the CLI `check-file` command.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchRequest {
    #[serde(default)]
    pub model_path: Option<PathBuf>,
    #[serde(default)]
    pub formula_path: Option<PathBuf>,
    #[serde(default)]
    pub model: Option<Model>,
    #[serde(default)]
    pub formula: Option<Formula>,
    #[serde(flatten)]
    pub rest: CheckRequest,
}

pub fn run_check(model: &Model, request: CheckRequest) -> QeResult<CheckResponse> {
    let run_id = request
        .run_id
        .clone()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(new_run_id);
    let model_name = request
        .model_name
        .clone()
        .unwrap_or_else(|| "inline-model".to_string());
    let budget = request.budget.unwrap_or(u64::MAX);

    model.validate()?;
    let resolved = model.check_formula(&request.formula)?;
    if !resolved.free_vars().is_empty() {
        return Err(QeError::input(
            "free_variable",
            format!(
                "formula has free variables {}; only closed sentences can be evaluated",
                resolved.free_vars().join(", ")
            ),
        ));
    }

    let mut logger = VecLogger::new();
    logger.note(format!(
        "start {} allow_empty_domain={} budget={} node_cap={}",
        run_id, request.allow_empty_domain, budget, request.node_cap
    ));

    let report = eliminate(model, &resolved, budget, request.node_cap, &mut logger)?;

    let verdict = evaluate_expanded(
        model,
        &report.formula,
        request.allow_empty_domain,
        &mut logger,
    )?;

    // The residual case never evaluates over an empty sort, but a definite
    // result may, so empty-domain policy is enforced right here as a dedicated
    // pre-pass on the resolved sentence as well (giving the same answer and a
    // precise error when disallowed).
    if !request.allow_empty_domain {
        enforce_nonempty_domains(model, &resolved)?;
    }

    let reason = match verdict {
        Verdict::Unknown => {
            format!(
                "budget {} exhausted after {} instantiations; {} quantifier(s) retained",
                budget,
                report.used,
                report.formula.quantifier_count()
            )
        }
        Verdict::True | Verdict::False => format!(
            "fully expanded with {} instantiations; ground evaluation is {}",
            report.used,
            verdict.as_str()
        ),
    };

    let proof = ProofRecord {
        run_id: run_id.clone(),
        created_unix_nanos: now_nanos(),
        model_name,
        original_formula: resolved.clone(),
        allow_empty_domain: request.allow_empty_domain,
        instantiation_budget: budget,
        instantiations_used: report.used,
        node_cap: request.node_cap,
        expanded_formula: report.formula.clone(),
        residual_quantifiers: report.formula.quantifier_count(),
        verdict,
        reason: reason.clone(),
        steps: logger.steps.clone(),
    };

    let check_report = if request.verify {
        Some(check(model, &proof)?)
    } else {
        None
    };

    Ok(CheckResponse {
        run_id,
        verdict,
        reason,
        instantiations_used: report.used,
        instantiation_budget: budget,
        residual_quantifiers: proof.residual_quantifiers,
        expanded_formula: report.formula,
        proof,
        check_report,
    })
}

/// Load a model either from `model_path` or inline JSON.
pub fn load_model(req: &BatchRequest) -> QeResult<Model> {
    if let Some(path) = &req.model_path {
        read_json(path)
    } else if let Some(model) = &req.model {
        Ok(model.clone())
    } else {
        Err(QeError::input(
            "missing_model",
            "request must provide either model_path or inline model",
        ))
    }
}

/// Load a formula either from `formula_path` or the inline field.
pub fn load_formula(req: &BatchRequest) -> QeResult<Formula> {
    if let Some(path) = &req.formula_path {
        read_json(path)
    } else if let Some(formula) = &req.formula {
        Ok(formula.clone())
    } else {
        Err(QeError::input(
            "missing_formula",
            "request must provide either formula_path or inline formula",
        ))
    }
}

pub fn run_batch(mut req: BatchRequest) -> QeResult<CheckResponse> {
    let model = load_model(&req)?;
    let formula = load_formula(&req)?;
    req.rest.formula = formula;
    run_check(&model, req.rest)
}

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> QeResult<T> {
    let bytes = fs::read(path).map_err(|e| {
        QeError::input(
            "fixture_unreadable",
            format!("cannot read {}: {}", path.display(), e),
        )
    })?;
    serde_json::from_slice(&bytes).map_err(|e| {
        QeError::input(
            "fixture_invalid_json",
            format!("invalid JSON in {}: {}", path.display(), e),
        )
    })
}

fn enforce_nonempty_domains(model: &Model, formula: &Formula) -> QeResult<()> {
    fn walk(model: &Model, f: &Formula) -> QeResult<()> {
        match f {
            Formula::Bool { .. } | Formula::Pred { .. } | Formula::Eq { .. } => Ok(()),
            Formula::Not { inner } => walk(model, inner),
            Formula::And { children } | Formula::Or { children } => {
                for c in children {
                    walk(model, c)?;
                }
                Ok(())
            }
            Formula::Impl { left, right } | Formula::Iff { left, right } => {
                walk(model, left)?;
                walk(model, right)
            }
            Formula::Forall { sort, .. } | Formula::Exists { sort, .. } => {
                if model
                    .sort(sort)
                    .map(|s| s.elements.is_empty())
                    .unwrap_or(true)
                {
                    return Err(QeError::computation(
                        "empty_domain",
                        format!(
                            "quantifier binds empty sort {}; empty domains are disallowed",
                            sort
                        ),
                    ));
                }
                // Recurse to catch nested empty sorts too.
                match f {
                    Formula::Forall { inner, .. } | Formula::Exists { inner, .. } => {
                        walk(model, inner)
                    }
                    _ => unreachable!(),
                }
            }
        }
    }
    walk(model, formula)
}
