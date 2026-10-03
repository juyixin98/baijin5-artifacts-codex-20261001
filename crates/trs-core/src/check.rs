//! The certificate checking algorithm.
//!
//! A certificate is accepted (verdict `terminating`) iff
//!  1. every interpretation is strictly monotone in every argument
//!     (all non-constant coefficients >= 1), and
//!  2. for every rule `l -> r`, the evaluated linear polynomials satisfy
//!     `[l] - [r] >= 1` for all natural valuations, which for linear
//!     polynomials over N is decidable exactly: every variable coefficient
//!     difference must be >= 0 and the constant difference must be >= 1.
//!
//! Anything else yields verdict `rejected` with explicit failure reasons.
//! Rejection is never a non-termination claim.

use crate::eval::evaluate;
use crate::interpret::InterpMap;
use std::collections::BTreeSet;
use trs_proof::{
    compute_run_id, Failure, LinearPoly, MonotonicityEntry, ProofRecord, RuleProof, Verdict,
};
use trs_syntax::{validate_certificate, validate_system, Certificate, Error, Limits, System};

/// Check `cert` as a termination certificate for `system`.
///
/// Returns the full proof record (accepted or rejected). `Err` is reserved
/// for input errors, resource exhaustion and computation failures — never
/// for "the certificate does not prove termination".
pub fn check_certificate(
    system: &System,
    cert: &Certificate,
    limits: &Limits,
) -> Result<ProofRecord, Error> {
    let signature = validate_system(system, limits)?;
    validate_certificate(cert, system, &signature, limits)?;
    let interp = InterpMap::from_certificate(cert);

    let mut failures: Vec<Failure> = Vec::new();
    let mut monotonicity = Vec::new();
    for symbol in signature.symbols() {
        let (constant, coeffs) = interp
            .get(symbol)
            .expect("certificate validated against signature");
        let violating: Vec<usize> = coeffs
            .iter()
            .enumerate()
            .filter(|(_, coeff)| **coeff == 0)
            .map(|(index, _)| index)
            .collect();
        for argument in &violating {
            failures.push(Failure::MonotonicityViolation {
                symbol: symbol.clone(),
                argument: *argument,
                coefficient: 0,
            });
        }
        monotonicity.push(MonotonicityEntry {
            symbol: symbol.clone(),
            constant: *constant,
            coefficients: coeffs.clone(),
            strict: violating.is_empty(),
            violating_arguments: violating,
        });
    }

    let mut rule_proofs = Vec::new();
    for rule in &system.rules {
        let lhs = evaluate(&rule.lhs, &interp, limits, 1)?;
        let rhs = evaluate(&rule.rhs, &interp, limits, 1)?;
        let proof = compare(&rule.name, lhs, rhs);
        if !proof.decreases {
            failures.push(Failure::RuleNotDecreasing {
                rule: rule.name.clone(),
                constant_difference: proof.constant_difference,
                negative_coefficients: proof
                    .coefficient_differences
                    .iter()
                    .filter(|(_, diff)| **diff < 0)
                    .map(|(var, _)| var.clone())
                    .collect(),
            });
        }
        rule_proofs.push(proof);
    }

    let verdict = if failures.is_empty() {
        Verdict::Terminating
    } else {
        Verdict::Rejected
    };
    Ok(ProofRecord {
        run_id: compute_run_id(system, cert),
        system: system.name.clone(),
        verdict,
        failures,
        monotonicity,
        rules: rule_proofs,
    })
}

/// Compare the two sides of a rule: `decreases` iff the constant difference
/// is strictly positive and every coefficient difference is non-negative.
fn compare(rule: &str, lhs: LinearPoly, rhs: LinearPoly) -> RuleProof {
    let vars: BTreeSet<String> = lhs
        .coefficients
        .keys()
        .chain(rhs.coefficients.keys())
        .cloned()
        .collect();
    let mut differences = std::collections::BTreeMap::new();
    for var in &vars {
        differences.insert(
            var.clone(),
            lhs.coefficient_of(var) as i128 - rhs.coefficient_of(var) as i128,
        );
    }
    let constant_difference = lhs.constant as i128 - rhs.constant as i128;
    let decreases = constant_difference > 0 && differences.values().all(|diff| *diff >= 0);

    let mut problems = Vec::new();
    if constant_difference <= 0 {
        problems.push(format!(
            "constant difference {} is not strictly positive",
            constant_difference
        ));
    }
    let negative: Vec<String> = differences
        .iter()
        .filter(|(_, diff)| **diff < 0)
        .map(|(var, diff)| format!("{} ({})", var, diff))
        .collect();
    if !negative.is_empty() {
        problems.push(format!(
            "negative coefficient difference(s): {}",
            negative.join(", ")
        ));
    }
    let reason = if problems.is_empty() {
        format!(
            "constant difference {} > 0 and all coefficient differences are non-negative",
            constant_difference
        )
    } else {
        problems.join("; ")
    };

    RuleProof {
        rule: rule.to_string(),
        lhs,
        rhs,
        coefficient_differences: differences,
        constant_difference,
        decreases,
        reason,
    }
}
