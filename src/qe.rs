//! Quantifier elimination by expansion over the finite enumerated domain.
//!
//! `forall x. phi` becomes the conjunction of `phi[c/x]` for every domain
//! element `c`; `exists x. phi` becomes the disjunction. Over an empty
//! domain (only reachable when the model policy allows it) they become
//! `true` / `false` respectively.
//!
//! Budget contract: the budget counts quantifier expansions. Each expanded
//! quantifier consumes one unit, shared globally and consumed left to
//! right. When the budget is exhausted, remaining quantifiers are kept
//! unexpanded in the output formula and the outcome is marked
//! [`QeStatus::Partial`] with `unknown = true`. `None` means unlimited.

use crate::error::QeError;
use crate::model::Model;
use crate::proof::{new_run_id, Action, ProofStep, QuantKind};
use crate::syntax::{Formula, Term};
use serde::Serialize;

/// Hard recursion-depth limit for the eliminator. Deliberately conservative
/// so that debug-build stack frames cannot overflow the default 2 MiB test
/// thread stack before the limit is reported as resource exhaustion.
pub const MAX_QE_DEPTH: u32 = 128;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum QeStatus {
    /// All quantifiers were eliminated; the result is quantifier-free.
    Eliminated,
    /// Budget ran out; unprocessed quantifiers remain and the truth of the
    /// original formula cannot be read off the (partial) expansion alone.
    Partial,
}

#[derive(Debug, Clone, Serialize)]
pub struct QeOutcome {
    pub run_id: String,
    pub status: QeStatus,
    /// True exactly when the status is `Partial`.
    pub unknown: bool,
    pub formula: Formula,
    pub remaining_quantifiers: usize,
    pub trace: Vec<ProofStep>,
}

pub struct Eliminator<'m> {
    model: &'m Model,
    budget: Option<u64>,
    run_id: String,
    steps: Vec<ProofStep>,
}

impl<'m> Eliminator<'m> {
    pub fn new(model: &'m Model, budget: Option<u64>) -> Self {
        Eliminator {
            model,
            budget,
            run_id: new_run_id(),
            steps: Vec::new(),
        }
    }

    pub fn run(mut self, formula: &Formula) -> Result<QeOutcome, QeError> {
        self.model.validate()?;
        let free = formula.free_vars();
        if !free.is_empty() {
            let names: Vec<String> = free.into_iter().collect();
            return Err(QeError::state_conflict(format!(
                "cannot eliminate quantifiers of a formula with unbound free variables: {}",
                names.join(", ")
            )));
        }
        let result = self.eliminate(formula, 0)?;
        let remaining = result.quantifier_count();
        let status = if remaining == 0 {
            QeStatus::Eliminated
        } else {
            QeStatus::Partial
        };
        Ok(QeOutcome {
            run_id: self.run_id.clone(),
            status,
            unknown: status == QeStatus::Partial,
            formula: result,
            remaining_quantifiers: remaining,
            trace: self.steps,
        })
    }

    fn record(&mut self, depth: u32, action: Action, reason: String) {
        let seq = self.steps.len() as u64;
        self.steps.push(ProofStep {
            run_id: self.run_id.clone(),
            seq,
            depth,
            action,
            reason,
        });
    }

    fn eliminate(&mut self, formula: &Formula, depth: u32) -> Result<Formula, QeError> {
        if depth > MAX_QE_DEPTH {
            return Err(QeError::resource_exhausted(format!(
                "elimination recursion depth exceeded {MAX_QE_DEPTH}"
            )));
        }
        match formula {
            Formula::True | Formula::False | Formula::Atom { .. } | Formula::Eq { .. } => {
                Ok(formula.clone())
            }
            Formula::Not { arg } => Ok(Formula::not(self.eliminate(arg, depth + 1)?)),
            Formula::And { args } => {
                let mut out = Vec::with_capacity(args.len());
                for a in args {
                    out.push(self.eliminate(a, depth + 1)?);
                }
                Ok(Formula::and(out))
            }
            Formula::Or { args } => {
                let mut out = Vec::with_capacity(args.len());
                for a in args {
                    out.push(self.eliminate(a, depth + 1)?);
                }
                Ok(Formula::or(out))
            }
            Formula::Implies { left, right } => Ok(Formula::Implies {
                left: Box::new(self.eliminate(left, depth + 1)?),
                right: Box::new(self.eliminate(right, depth + 1)?),
            }),
            Formula::Iff { left, right } => Ok(Formula::Iff {
                left: Box::new(self.eliminate(left, depth + 1)?),
                right: Box::new(self.eliminate(right, depth + 1)?),
            }),
            Formula::Forall { var, body } => {
                self.eliminate_quantifier(QuantKind::Forall, var, body, depth)
            }
            Formula::Exists { var, body } => {
                self.eliminate_quantifier(QuantKind::Exists, var, body, depth)
            }
        }
    }

    fn eliminate_quantifier(
        &mut self,
        kind: QuantKind,
        var: &str,
        body: &Formula,
        depth: u32,
    ) -> Result<Formula, QeError> {
        if let Some(remaining) = self.budget {
            if remaining == 0 {
                self.record(
                    depth,
                    Action::BudgetExhausted {
                        kind,
                        var: var.to_string(),
                    },
                    format!(
                        "expansion budget exhausted; {} quantifier over '{}' kept unexpanded and result marked unknown",
                        kind.symbol(),
                        var
                    ),
                );
                let kept = match kind {
                    QuantKind::Forall => Formula::forall(var, body.clone()),
                    QuantKind::Exists => Formula::exists(var, body.clone()),
                };
                return Ok(kept);
            }
            self.budget = Some(remaining - 1);
        }

        if self.model.domain.is_empty() {
            // Only reachable when the model explicitly allows an empty domain.
            self.record(
                depth,
                Action::EmptyDomainExpansion {
                    kind,
                    var: var.to_string(),
                },
                format!(
                    "domain is empty; {} quantifier over '{}' expands to the vacuous constant",
                    kind.symbol(),
                    var
                ),
            );
            return Ok(match kind {
                QuantKind::Forall => Formula::True,
                QuantKind::Exists => Formula::False,
            });
        }

        let mut parts = Vec::with_capacity(self.model.domain.len());
        for el in &self.model.domain {
            // Capture-avoiding substitution: constants live in their own
            // namespace, and variable-for-variable collisions are renamed
            // inside `subst`.
            let instantiated = body.subst(var, &Term::Const(el.clone()));
            parts.push(self.eliminate(&instantiated, depth + 1)?);
        }
        let combined = match kind {
            QuantKind::Forall => Formula::and(parts),
            QuantKind::Exists => Formula::or(parts),
        };
        self.record(
            depth,
            Action::Expand {
                kind,
                var: var.to_string(),
                domain_size: self.model.domain.len(),
                nodes_after: combined.node_count(),
            },
            format!(
                "expanded {} quantifier over '{}' across {} domain element(s)",
                kind.symbol(),
                var,
                self.model.domain.len()
            ),
        );
        Ok(combined)
    }
}
