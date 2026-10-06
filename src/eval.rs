//! Model checking: direct recursive evaluation of a formula in a model.
//!
//! This module is deliberately independent of the quantifier-elimination
//! core so it can serve as the reference implementation in cross-checks.
//! Evaluation requires a closed formula; free variables are reported as
//! [`crate::error::ErrorKind::StateConflict`] because the assignment state
//! is insufficient.

use crate::error::QeError;
use crate::model::Model;
use crate::syntax::{Formula, Term};
use std::collections::HashMap;

/// Hard recursion-depth limit; exceeding it is reported as resource
/// exhaustion rather than risking a stack overflow.
pub const MAX_EVAL_DEPTH: u32 = 256;

pub type Env = HashMap<String, String>;

/// Evaluate a closed formula, validating the model first.
pub fn eval_closed(model: &Model, formula: &Formula) -> Result<bool, QeError> {
    model.validate()?;
    let free = formula.free_vars();
    if !free.is_empty() {
        let names: Vec<String> = free.into_iter().collect();
        return Err(QeError::state_conflict(format!(
            "formula has unbound free variables: {}",
            names.join(", ")
        )));
    }
    let mut env = Env::new();
    eval_rec(model, formula, &mut env, 0)
}

fn eval_rec(
    model: &Model,
    formula: &Formula,
    env: &mut Env,
    depth: u32,
) -> Result<bool, QeError> {
    if depth > MAX_EVAL_DEPTH {
        return Err(QeError::resource_exhausted(format!(
            "evaluation recursion depth exceeded {MAX_EVAL_DEPTH}"
        )));
    }
    match formula {
        Formula::True => Ok(true),
        Formula::False => Ok(false),
        Formula::Atom { pred, args } => {
            let predicate = model.predicates.get(pred).ok_or_else(|| {
                QeError::invalid_input(format!("unknown predicate '{pred}'"))
            })?;
            if predicate.arity != args.len() {
                return Err(QeError::invalid_input(format!(
                    "predicate '{pred}' expects {} argument(s), got {}",
                    predicate.arity,
                    args.len()
                )));
            }
            let mut values = Vec::with_capacity(args.len());
            for t in args {
                values.push(resolve(model, t, env)?);
            }
            Ok(predicate.tuples.contains(&values))
        }
        Formula::Eq { left, right } => {
            let l = resolve(model, left, env)?;
            let r = resolve(model, right, env)?;
            Ok(l == r)
        }
        Formula::Not { arg } => Ok(!eval_rec(model, arg, env, depth + 1)?),
        Formula::And { args } => {
            for a in args {
                if !eval_rec(model, a, env, depth + 1)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        Formula::Or { args } => {
            for a in args {
                if eval_rec(model, a, env, depth + 1)? {
                    return Ok(true);
                }
            }
            Ok(false)
        }
        Formula::Implies { left, right } => {
            Ok(!eval_rec(model, left, env, depth + 1)?
                || eval_rec(model, right, env, depth + 1)?)
        }
        Formula::Iff { left, right } => {
            let l = eval_rec(model, left, env, depth + 1)?;
            let r = eval_rec(model, right, env, depth + 1)?;
            Ok(l == r)
        }
        Formula::Forall { var, body } => {
            // Empty domain (only reachable when the model policy allows it):
            // universal quantification is vacuously true.
            for el in &model.domain {
                let old = env.insert(var.clone(), el.clone());
                let value = eval_rec(model, body, env, depth + 1);
                restore(env, var, old);
                if !value? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        Formula::Exists { var, body } => {
            // Empty domain: existential quantification is false.
            for el in &model.domain {
                let old = env.insert(var.clone(), el.clone());
                let value = eval_rec(model, body, env, depth + 1);
                restore(env, var, old);
                if value? {
                    return Ok(true);
                }
            }
            Ok(false)
        }
    }
}

fn restore(env: &mut Env, var: &str, old: Option<String>) {
    match old {
        Some(v) => {
            env.insert(var.to_string(), v);
        }
        None => {
            env.remove(var);
        }
    }
}

fn resolve(model: &Model, term: &Term, env: &Env) -> Result<String, QeError> {
    match term {
        Term::Const(c) => {
            if model.is_domain_element(c) {
                Ok(c.clone())
            } else {
                Err(QeError::invalid_input(format!(
                    "constant '{c}' is not a domain element"
                )))
            }
        }
        Term::Var(v) => env
            .get(v)
            .cloned()
            .ok_or_else(|| QeError::state_conflict(format!("unbound variable '{v}'"))),
    }
}
