//! Independent checker.
//!
//! This crate deliberately does NOT depend on the inference core
//! (`trs-core`). It re-implements polynomial evaluation and the decrease
//! judgment from scratch (different internal representation, own arithmetic
//! path), recomputes the verdict for a (system, certificate) pair, and audits
//! a proof record against that recomputation. Any disagreement is reported
//! as a list of mismatches — a state conflict between the record and the
//! inputs it claims to cover.

#![forbid(unsafe_code)]

use serde::Serialize;
use std::collections::{BTreeSet, HashMap};
use trs_proof::{compute_run_id, LinearPoly, ProofRecord, Verdict};
use trs_syntax::{validate_certificate, validate_system, Certificate, Error, ErrorKind, Limits, System, Term};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Agreement {
    /// The record faithfully reflects an independent recomputation.
    Agree,
    /// The record conflicts with the inputs (state conflict).
    Disagree,
}

#[derive(Debug, Clone, Serialize)]
pub struct VerifyReport {
    /// Run id recomputed from the inputs.
    pub run_id: String,
    /// Run id stored in the presented record.
    pub record_run_id: String,
    pub agreement: Agreement,
    /// The independently recomputed verdict.
    pub verdict: Verdict,
    /// Every discrepancy found, human-readable and replayable.
    pub mismatches: Vec<String>,
}

/// Re-derive the verdict for `system` + `cert` and audit `record` against it.
pub fn verify_certificate(
    system: &System,
    cert: &Certificate,
    record: &ProofRecord,
    limits: &Limits,
) -> Result<VerifyReport, Error> {
    let signature = validate_system(system, limits)?;
    validate_certificate(cert, system, &signature, limits)?;

    let interp: HashMap<String, (u64, Vec<u64>)> = cert
        .interpretation
        .iter()
        .map(|entry| (entry.symbol.clone(), (entry.constant, entry.coeffs.clone())))
        .collect();

    let run_id = compute_run_id(system, cert);
    let mut mismatches = Vec::new();

    if record.run_id != run_id {
        mismatches.push(format!(
            "run id mismatch: the inputs hash to '{}' but the record claims '{}'",
            run_id, record.run_id
        ));
    }
    if record.system != system.name {
        mismatches.push(format!(
            "system name mismatch: input system is '{}' but the record says '{}'",
            system.name, record.system
        ));
    }

    // Independent strict-monotonicity judgment.
    let mut monotone = true;
    for symbol in signature.symbols() {
        let (_, coeffs) = interp
            .get(symbol)
            .expect("certificate validated against signature");
        let strict_here = coeffs.iter().all(|coeff| *coeff >= 1);
        if !strict_here {
            monotone = false;
        }
        match record.monotonicity.iter().find(|m| &m.symbol == symbol) {
            None => mismatches.push(format!(
                "record has no monotonicity entry for symbol '{}'",
                symbol
            )),
            Some(entry) => {
                if entry.strict != strict_here {
                    mismatches.push(format!(
                        "monotonicity mismatch for '{}': recomputed strict={} but the record says {}",
                        symbol, strict_here, entry.strict
                    ));
                }
            }
        }
    }
    for entry in &record.monotonicity {
        if signature.arity(&entry.symbol).is_none() {
            mismatches.push(format!(
                "record monotonicity mentions '{}' which is not in the signature",
                entry.symbol
            ));
        }
    }

    // Independent per-rule decrease judgment.
    let mut all_decrease = true;
    let mut own_rules: Vec<(String, LinearPoly, LinearPoly, bool)> = Vec::new();
    for rule in &system.rules {
        let lhs = to_linear(eval_term(&rule.lhs, &interp, limits, 1)?);
        let rhs = to_linear(eval_term(&rule.rhs, &interp, limits, 1)?);
        let decreases = strictly_decreases(&lhs, &rhs);
        if !decreases {
            all_decrease = false;
        }
        own_rules.push((rule.name.clone(), lhs, rhs, decreases));
    }
    let own_verdict = if monotone && all_decrease {
        Verdict::Terminating
    } else {
        Verdict::Rejected
    };

    if own_verdict != record.verdict {
        mismatches.push(format!(
            "verdict mismatch: recomputed '{}' but the record says '{}'",
            own_verdict, record.verdict
        ));
    }

    for (name, lhs, rhs, decreases) in &own_rules {
        match record.rules.iter().find(|rp| &rp.rule == name) {
            None => mismatches.push(format!("record has no proof for rule '{}'", name)),
            Some(rp) => {
                if rp.decreases != *decreases {
                    mismatches.push(format!(
                        "rule '{}': recomputed decreases={} but the record says {}",
                        name, decreases, rp.decreases
                    ));
                }
                if rp.lhs != *lhs {
                    mismatches.push(format!(
                        "rule '{}': recomputed lhs = {} but the record says {}",
                        name, lhs, rp.lhs
                    ));
                }
                if rp.rhs != *rhs {
                    mismatches.push(format!(
                        "rule '{}': recomputed rhs = {} but the record says {}",
                        name, rhs, rp.rhs
                    ));
                }
            }
        }
    }
    for rp in &record.rules {
        if !system.rules.iter().any(|rule| rule.name == rp.rule) {
            mismatches.push(format!(
                "record proves rule '{}' which does not exist in the system",
                rp.rule
            ));
        }
    }

    let agreement = if mismatches.is_empty() {
        Agreement::Agree
    } else {
        Agreement::Disagree
    };
    Ok(VerifyReport {
        run_id,
        record_run_id: record.run_id.clone(),
        agreement,
        verdict: own_verdict,
        mismatches,
    })
}

/// Independent polynomial representation: constant plus a hash map.
struct RawPoly {
    konst: u64,
    coeffs: HashMap<String, u64>,
}

impl RawPoly {
    fn constant(value: u64) -> Self {
        RawPoly {
            konst: value,
            coeffs: HashMap::new(),
        }
    }

    /// `self += scale * other`, checked against the resource limits.
    fn add_scaled(&mut self, other: &RawPoly, scale: u64, limits: &Limits) -> Result<(), Error> {
        self.konst = checked_combine(self.konst, other.konst, scale, limits)?;
        for (var, coeff) in &other.coeffs {
            let merged = checked_combine(
                self.coeffs.get(var).copied().unwrap_or(0),
                *coeff,
                scale,
                limits,
            )?;
            if merged == 0 {
                self.coeffs.remove(var);
            } else {
                self.coeffs.insert(var.clone(), merged);
            }
        }
        Ok(())
    }
}

fn checked_combine(base: u64, value: u64, scale: u64, limits: &Limits) -> Result<u64, Error> {
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

fn eval_term(
    term: &Term,
    interp: &HashMap<String, (u64, Vec<u64>)>,
    limits: &Limits,
    depth: usize,
) -> Result<RawPoly, Error> {
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
        Term::Var { var } => {
            let mut poly = RawPoly::constant(0);
            poly.coeffs.insert(var.clone(), 1);
            Ok(poly)
        }
        Term::Fun { fun, args } => {
            let (constant, coeffs) = interp.get(fun).ok_or_else(|| {
                Error::new(
                    ErrorKind::UnknownSymbol,
                    format!("no interpretation for symbol '{}'", fun),
                )
            })?;
            let mut acc = RawPoly::constant(*constant);
            for (arg, coeff) in args.iter().zip(coeffs.iter()) {
                let sub = eval_term(arg, interp, limits, depth + 1)?;
                acc.add_scaled(&sub, *coeff, limits)?;
            }
            Ok(acc)
        }
    }
}

fn to_linear(poly: RawPoly) -> LinearPoly {
    LinearPoly {
        constant: poly.konst,
        coefficients: poly.coeffs.into_iter().collect(),
    }
}

/// Exact decrease test for linear polynomials over the naturals:
/// `[l] > [r]` for all valuations iff the constant strictly decreases and no
/// variable coefficient decreases.
fn strictly_decreases(lhs: &LinearPoly, rhs: &LinearPoly) -> bool {
    if lhs.constant <= rhs.constant {
        return false;
    }
    let vars: BTreeSet<&String> = lhs
        .coefficients
        .keys()
        .chain(rhs.coefficients.keys())
        .collect();
    vars.into_iter()
        .all(|var| lhs.coefficient_of(var) >= rhs.coefficient_of(var))
}

#[cfg(test)]
mod tests {
    use super::*;
    use trs_core::check_certificate;
    use trs_proof::{Failure, MonotonicityEntry, RuleProof, Verdict};

    const UNARY_SYSTEM: &str = r#"{
        "name": "unary",
        "signature": [{"name": "f", "arity": 1}],
        "rules": [{"name": "ff",
            "lhs": {"fun": "f", "args": [{"fun": "f", "args": [{"var": "x"}]}]},
            "rhs": {"var": "x"}}]
    }"#;

    const UNARY_CERT: &str = r#"{
        "system": "unary",
        "interpretation": [{"symbol": "f", "const": 1, "coeffs": [1]}]
    }"#;

    fn unary() -> (System, Certificate) {
        (
            serde_json::from_str(UNARY_SYSTEM).unwrap(),
            serde_json::from_str(UNARY_CERT).unwrap(),
        )
    }

    /// A fully hand-written proof record (only the run id, a pure input hash,
    /// is computed). The verifier must agree with it.
    #[test]
    fn agrees_with_handwritten_record() {
        let (system, cert) = unary();
        let record = ProofRecord {
            run_id: compute_run_id(&system, &cert),
            system: "unary".to_string(),
            verdict: Verdict::Terminating,
            failures: vec![],
            monotonicity: vec![MonotonicityEntry {
                symbol: "f".to_string(),
                constant: 1,
                coefficients: vec![1],
                violating_arguments: vec![],
                strict: true,
            }],
            rules: vec![RuleProof {
                rule: "ff".to_string(),
                lhs: LinearPoly {
                    constant: 2,
                    coefficients: [("x".to_string(), 1u64)].into_iter().collect(),
                },
                rhs: LinearPoly {
                    constant: 0,
                    coefficients: [("x".to_string(), 1u64)].into_iter().collect(),
                },
                coefficient_differences: [("x".to_string(), 0i128)].into_iter().collect(),
                constant_difference: 2,
                decreases: true,
                reason: "hand-written reference".to_string(),
            }],
        };
        let report = verify_certificate(&system, &cert, &record, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Agree, "{:?}", report.mismatches);
        assert_eq!(report.verdict, Verdict::Terminating);
        assert!(report.mismatches.is_empty());
    }

    #[test]
    fn agrees_with_core_record_on_valid_and_rejected_certs() {
        let (system, cert) = unary();
        let record = check_certificate(&system, &cert, &Limits::default()).unwrap();
        let report = verify_certificate(&system, &cert, &record, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Agree, "{:?}", report.mismatches);

        // Rejected certificate: agreement must still be possible — rejection
        // is a judgment, not an error.
        let bad_cert: Certificate = serde_json::from_str(
            r#"{"system": "unary", "interpretation": [{"symbol": "f", "const": 1, "coeffs": [0]}]}"#,
        )
        .unwrap();
        let record = check_certificate(&system, &bad_cert, &Limits::default()).unwrap();
        assert_eq!(record.verdict, Verdict::Rejected);
        let report = verify_certificate(&system, &bad_cert, &record, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Agree, "{:?}", report.mismatches);
        assert_eq!(report.verdict, Verdict::Rejected);
    }

    #[test]
    fn detects_tampered_verdict() {
        let (system, cert) = unary();
        let mut record = check_certificate(&system, &cert, &Limits::default()).unwrap();
        record.verdict = Verdict::Rejected;
        let report = verify_certificate(&system, &cert, &record, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Disagree);
        assert!(report.mismatches.iter().any(|m| m.contains("verdict mismatch")));
    }

    #[test]
    fn detects_tampered_polynomial() {
        let (system, cert) = unary();
        let mut record = check_certificate(&system, &cert, &Limits::default()).unwrap();
        record.rules[0].lhs.constant = 99;
        let report = verify_certificate(&system, &cert, &record, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Disagree);
        assert!(report.mismatches.iter().any(|m| m.contains("recomputed lhs")));
    }

    #[test]
    fn detects_foreign_run_id() {
        let (system, cert) = unary();
        let mut record = check_certificate(&system, &cert, &Limits::default()).unwrap();
        record.run_id = "run-00000000deadbeef".to_string();
        let report = verify_certificate(&system, &cert, &record, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Disagree);
        assert!(report.mismatches.iter().any(|m| m.contains("run id mismatch")));
    }

    #[test]
    fn detects_missing_and_extra_rule_proofs() {
        let (system, cert) = unary();
        let record = check_certificate(&system, &cert, &Limits::default()).unwrap();

        let mut missing = record.clone();
        missing.rules.clear();
        let report = verify_certificate(&system, &cert, &missing, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Disagree);
        assert!(report.mismatches.iter().any(|m| m.contains("no proof for rule 'ff'")));

        let mut extra = record;
        extra.rules.push(RuleProof {
            rule: "ghost".to_string(),
            lhs: LinearPoly::constant(1),
            rhs: LinearPoly::constant(0),
            coefficient_differences: Default::default(),
            constant_difference: 1,
            decreases: true,
            reason: "invented".to_string(),
        });
        let report = verify_certificate(&system, &cert, &extra, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Disagree);
        assert!(report
            .mismatches
            .iter()
            .any(|m| m.contains("rule 'ghost' which does not exist")));
    }

    #[test]
    fn detects_forged_failure_list() {
        // A record that claims rejection while everything actually decreases.
        let (system, cert) = unary();
        let mut record = check_certificate(&system, &cert, &Limits::default()).unwrap();
        record.verdict = Verdict::Rejected;
        record.failures = vec![Failure::MonotonicityViolation {
            symbol: "f".to_string(),
            argument: 0,
            coefficient: 0,
        }];
        let report = verify_certificate(&system, &cert, &record, &Limits::default()).unwrap();
        assert_eq!(report.agreement, Agreement::Disagree);
    }
}
