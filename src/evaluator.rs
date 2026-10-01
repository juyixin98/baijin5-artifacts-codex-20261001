//! Model theoretic evaluator.
//!
//! `evaluate_quantified` directly recurses the quantifiers over a finite
//! environment; it is the independent reference semantics used to cross-check
//! expansion. `evaluate_expanded` evaluates a formula produced by the
//! elimination step and reports `Unknown` whenever residual quantifiers
//! remain.

use std::collections::BTreeMap;

use crate::error::{QeError, QeResult};
use crate::model::Model;
use crate::proof::{RunLogger, Step, Verdict};
use crate::syntax::{Formula, Term};

/// Reference semantics for a formula directly over the model.
///
/// `allow_empty_domain = false` makes evaluation over any empty sort a
/// computation failure; `true` uses the standard `forall = true`,
/// `exists = false` convention for an empty binding sort.
pub fn evaluate_quantified(
    model: &Model,
    formula: &Formula,
    env: &BTreeMap<String, String>,
    allow_empty_domain: bool,
    logger: &mut dyn RunLogger,
) -> QeResult<bool> {
    match formula {
        Formula::Bool { value: v } => Ok(*v),
        Formula::Pred { name, args } => {
            let mut values = Vec::with_capacity(args.len());
            for arg in args {
                values.push(eval_term(model, arg, env, logger)?);
            }
            let predicate = model.predicate(name).expect("type checked predicate");
            let result = predicate.holds(&values);
            logger.emit(Step::EvalAtom {
                seq: 0,
                atom: format!("{}({})", name, values.join(",")),
                value: result,
            });
            Ok(result)
        }
        Formula::Eq { left, right } => {
            let l = eval_term(model, left, env, logger)?;
            let r = eval_term(model, right, env, logger)?;
            let result = l == r;
            logger.emit(Step::EvalAtom {
                seq: 0,
                atom: format!("{} = {}", l, r),
                value: result,
            });
            Ok(result)
        }
        Formula::Not { inner } => Ok(!evaluate_quantified(
            model,
            inner,
            env,
            allow_empty_domain,
            logger,
        )?),
        Formula::And { children } => {
            for child in children {
                if !evaluate_quantified(model, child, env, allow_empty_domain, logger)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        Formula::Or { children } => {
            for child in children {
                if evaluate_quantified(model, child, env, allow_empty_domain, logger)? {
                    return Ok(true);
                }
            }
            Ok(false)
        }
        Formula::Impl { left, right } => {
            let l = evaluate_quantified(model, left, env, allow_empty_domain, logger)?;
            Ok(!l || evaluate_quantified(model, right, env, allow_empty_domain, logger)?)
        }
        Formula::Iff { left, right } => {
            let l = evaluate_quantified(model, left, env, allow_empty_domain, logger)?;
            let r = evaluate_quantified(model, right, env, allow_empty_domain, logger)?;
            Ok(l == r)
        }
        Formula::Forall { var, sort, inner } => {
            let domain = &model
                .sort(sort)
                .ok_or_else(|| QeError::input("unknown_sort", format!("sort {}", sort)))?
                .elements;
            if domain.is_empty() && !allow_empty_domain {
                return Err(empty_domain_error(sort));
            }
            let mut local = env.clone();
            local.insert(var.clone(), String::new());
            for element in domain {
                local.insert(var.clone(), element.clone());
                if !evaluate_quantified(model, inner, &local, allow_empty_domain, logger)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        Formula::Exists { var, sort, inner } => {
            let domain = &model
                .sort(sort)
                .ok_or_else(|| QeError::input("unknown_sort", format!("sort {}", sort)))?
                .elements;
            if domain.is_empty() && !allow_empty_domain {
                return Err(empty_domain_error(sort));
            }
            let mut local = env.clone();
            local.insert(var.clone(), String::new());
            for element in domain {
                local.insert(var.clone(), element.clone());
                if evaluate_quantified(model, inner, &local, allow_empty_domain, logger)? {
                    return Ok(true);
                }
            }
            Ok(false)
        }
    }
}

/// Evaluate a formula returned by expansion. Residual quantifiers force
/// `Unknown` instead of a definite boolean answer.
pub fn evaluate_expanded(
    model: &Model,
    formula: &Formula,
    allow_empty_domain: bool,
    logger: &mut dyn RunLogger,
) -> QeResult<Verdict> {
    if !formula.is_quantifier_free() {
        let reason = format!(
            "{} quantifier node(s) remain after expansion; truth value is unknown",
            formula.quantifier_count()
        );
        logger.emit(Step::EvalFormula {
            seq: 0,
            verdict: Verdict::Unknown,
            reason: reason.clone(),
        });
        return Ok(Verdict::Unknown);
    }
    let env = BTreeMap::new();
    let value = evaluate_quantified(model, formula, &env, allow_empty_domain, logger)?;
    let verdict = if value { Verdict::True } else { Verdict::False };
    logger.emit(Step::EvalFormula {
        seq: 0,
        verdict,
        reason: "expanded formula is quantifier free".to_string(),
    });
    Ok(verdict)
}

fn eval_term(
    model: &Model,
    term: &Term,
    env: &BTreeMap<String, String>,
    logger: &mut dyn RunLogger,
) -> QeResult<String> {
    let value = match term {
        Term::Var { name } => env.get(name).cloned().ok_or_else(|| {
            QeError::computation("unbound_variable", format!("no value for {}", name))
        })?,
        Term::Elem { value, .. } => value.clone(),
        Term::Const { name } => model
            .constant(name)
            .expect("type checked constant")
            .value
            .clone(),
        Term::App { name, args } => {
            let mut values = Vec::with_capacity(args.len());
            for arg in args {
                values.push(eval_term(model, arg, env, logger)?);
            }
            let function = model.function(name).expect("type checked function");
            let key = crate::model::Function::tuple_key(&values);
            match function.table.get(&key) {
                Some(v) => v.clone(),
                None => {
                    return Err(QeError::computation(
                        "partial_function",
                        format!("function {} is undefined on tuple ({})", name, key),
                    ));
                }
            }
        }
    };
    logger.emit(Step::EvalTerm {
        seq: 0,
        term: render_term(term),
        value: value.clone(),
    });
    Ok(value)
}

fn render_term(term: &Term) -> String {
    match term {
        Term::Var { name } => name.clone(),
        Term::Elem { value, .. } => value.clone(),
        Term::Const { name } => name.clone(),
        Term::App { name, args } => {
            let inner: Vec<String> = args.iter().map(render_term).collect();
            format!("{}({})", name, inner.join(","))
        }
    }
}

fn empty_domain_error(sort: &str) -> QeError {
    QeError::computation(
        "empty_domain",
        format!(
            "sort {} is empty and empty domains are disallowed; set allow_empty_domain to opt in",
            sort
        ),
    )
}
