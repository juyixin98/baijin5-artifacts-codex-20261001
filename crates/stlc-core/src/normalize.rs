//! Strong normalization by leftmost-innermost reduction.
//!
//! Innermost order normalizes the function and argument before contracting,
//! which gives deterministic, hand-checkable traces. Every beta contraction
//! and every conditional contraction counts against one reduction budget,
//! reported separately from typing failures.

use serde::{Deserialize, Serialize};
use stlc_syntax::db::DbTerm;

use crate::error::NormError;
use crate::subst::beta_contract;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepKind {
    Beta,
    IfReduce,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Step {
    pub index: usize,
    pub kind: StepKind,
    /// The exact redex before contraction.
    pub redex: DbTerm,
    /// Contractum immediately after contraction.
    pub contractum: DbTerm,
    /// Whole term after the step and any subsequent normalization of it.
    pub term_after: DbTerm,
    pub budget_remaining: usize,
    pub reason: String,
}

pub struct Normalizer {
    pub limit: usize,
    pub used: usize,
    pub steps: Vec<Step>,
}

impl Normalizer {
    pub fn new(limit: usize) -> Self {
        Normalizer {
            limit,
            used: 0,
            steps: Vec::new(),
        }
    }

    fn tick(
        &mut self,
        kind: StepKind,
        redex: DbTerm,
        contractum: DbTerm,
        after: DbTerm,
        reason: String,
    ) -> Result<(), NormError> {
        if self.used >= self.limit {
            return Err(NormError::BudgetExhausted {
                limit: self.limit,
                steps_used: self.used,
                last_term_db: stlc_syntax::pretty::db_to_string(&after),
            });
        }
        self.used += 1;
        self.steps.push(Step {
            index: self.used,
            kind,
            redex,
            contractum,
            term_after: after,
            budget_remaining: self.limit - self.used,
            reason,
        });
        Ok(())
    }

    /// Normalize one term. Returns the normal form (possibly neutral) or a
    /// budget-exhausted error.
    pub fn normalize(&mut self, term: &DbTerm) -> Result<DbTerm, NormError> {
        match term {
            DbTerm::BVar { .. } | DbTerm::FVar { .. } | DbTerm::BoolLit { .. } => {
                Ok(term.clone())
            }
            DbTerm::Abs { param_ty, body } => Ok(DbTerm::Abs {
                param_ty: param_ty.clone(),
                body: Box::new(self.normalize(body)?),
            }),
            DbTerm::App { func, arg } => {
                let func_n = self.normalize(func)?;
                let arg_n = self.normalize(arg)?;
                match func_n {
                    DbTerm::Abs { param_ty, body } => {
                        let redex = DbTerm::App {
                            func: Box::new(DbTerm::Abs {
                                param_ty: param_ty.clone(),
                                body: body.clone(),
                            }),
                            arg: Box::new(arg_n.clone()),
                        };
                        let contractum = beta_contract(&body, &arg_n);
                        let after = self.normalize(&contractum)?;
                        self.tick(
                            StepKind::Beta,
                            redex,
                            contractum,
                            after.clone(),
                            "beta-contract applied abstraction: substitute argument, drop binder"
                                .to_string(),
                        )?;
                        Ok(after)
                    }
                    other => Ok(DbTerm::App {
                        func: Box::new(other),
                        arg: Box::new(arg_n),
                    }),
                }
            }
            DbTerm::If {
                cond,
                then,
                otherwise,
            } => {
                let cond_n = self.normalize(cond)?;
                match cond_n {
                    DbTerm::BoolLit { value } => {
                        let chosen = if value {
                            (**then).clone()
                        } else {
                            (**otherwise).clone()
                        };
                        let reduced = self.normalize(&chosen)?;
                        self.tick(
                            StepKind::IfReduce,
                            term.clone(),
                            chosen,
                            reduced.clone(),
                            format!(
                                "guard normalized to `{value}`, select the corresponding branch"
                            ),
                        )?;
                        Ok(reduced)
                    }
                    neutral_guard => {
                        let then_n = self.normalize(then)?;
                        let else_n = self.normalize(otherwise)?;
                        Ok(DbTerm::If {
                            cond: Box::new(neutral_guard),
                            then: Box::new(then_n),
                            otherwise: Box::new(else_n),
                        })
                    }
                }
            }
        }
    }
}
