//! Evaluation of terms to linear polynomials under a linear interpretation.
//!
//! `[x] = x` and `[f(t1..tn)] = a0 + sum_i ai * [ti]`; since every `[f]` is
//! linear, every term evaluates to a linear polynomial. All arithmetic is
//! checked: `u64` overflow is a computation failure, exceeding the configured
//! coefficient bound is resource exhaustion.

use crate::interpret::InterpMap;
use trs_proof::LinearPoly;
use trs_syntax::{Error, ErrorKind, Limits, Term};

pub(crate) fn evaluate(
    term: &Term,
    interp: &InterpMap,
    limits: &Limits,
    depth: usize,
) -> Result<LinearPoly, Error> {
    if depth > limits.max_term_depth {
        return Err(Error::new(
            ErrorKind::TermTooDeep,
            format!(
                "term nesting depth exceeds limit of {}",
                limits.max_term_depth
            ),
        ));
    }
    match term {
        Term::Var { var } => Ok(LinearPoly::variable(var)),
        Term::Fun { fun, args } => {
            let (constant, coeffs) = interp.get(fun).ok_or_else(|| {
                Error::new(
                    ErrorKind::UnknownSymbol,
                    format!("no interpretation for symbol '{}'", fun),
                )
            })?;
            let mut acc = LinearPoly::constant(*constant);
            for (arg, coeff) in args.iter().zip(coeffs.iter()) {
                let sub = evaluate(arg, interp, limits, depth + 1)?;
                acc = add_scaled(&acc, &sub, *coeff, limits)?;
            }
            Ok(acc)
        }
    }
}

/// `acc + scale * p`, keeping the zero-coefficient-free representation.
fn add_scaled(
    acc: &LinearPoly,
    p: &LinearPoly,
    scale: u64,
    limits: &Limits,
) -> Result<LinearPoly, Error> {
    let mut out = acc.clone();
    out.constant = combine(out.constant, p.constant, scale, limits)?;
    for (var, coeff) in &p.coefficients {
        let merged = combine(out.coefficient_of(var), *coeff, scale, limits)?;
        if merged == 0 {
            out.coefficients.remove(var);
        } else {
            out.coefficients.insert(var.clone(), merged);
        }
    }
    Ok(out)
}

/// `base + value * scale` with checked arithmetic and the coefficient bound.
fn combine(base: u64, value: u64, scale: u64, limits: &Limits) -> Result<u64, Error> {
    let scaled = value.checked_mul(scale).ok_or_else(|| {
        Error::new(
            ErrorKind::ArithmeticOverflow,
            format!("multiplication overflow: {} * {}", value, scale),
        )
    })?;
    let sum = base.checked_add(scaled).ok_or_else(|| {
        Error::new(
            ErrorKind::ArithmeticOverflow,
            format!("addition overflow: {} + {}", base, scaled),
        )
    })?;
    if sum > limits.max_coefficient {
        return Err(Error::new(
            ErrorKind::CoefficientLimit,
            format!(
                "coefficient {} exceeds configured limit {}",
                sum, limits.max_coefficient
            ),
        ));
    }
    Ok(sum)
}
