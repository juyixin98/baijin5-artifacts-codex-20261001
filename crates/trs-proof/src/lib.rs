//! Proof record data contract shared by the inference core (producer) and
//! the independent verifier (consumer/auditor).

#![forbid(unsafe_code)]

pub mod record;
pub mod runid;

pub use record::{Failure, LinearPoly, MonotonicityEntry, ProofRecord, RuleProof, Verdict};
pub use runid::compute_run_id;

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use trs_syntax::{Certificate, SymbolDecl, SymbolInterpretation, System};

    #[test]
    fn poly_display_is_stable_and_readable() {
        let mut coefficients = BTreeMap::new();
        coefficients.insert("x".to_string(), 2);
        coefficients.insert("y".to_string(), 1);
        let poly = LinearPoly {
            constant: 2,
            coefficients,
        };
        assert_eq!(poly.to_string(), "2 + 2*x + y");
        assert_eq!(LinearPoly::constant(7).to_string(), "7");
        assert_eq!(LinearPoly::variable("z").to_string(), "0 + z");
    }

    #[test]
    fn run_id_is_deterministic_and_input_sensitive() {
        let system = System {
            name: "t".to_string(),
            signature: vec![SymbolDecl {
                name: "f".to_string(),
                arity: 1,
            }],
            rules: vec![],
        };
        let cert = Certificate {
            system: "t".to_string(),
            interpretation: vec![SymbolInterpretation {
                symbol: "f".to_string(),
                constant: 1,
                coeffs: vec![1],
            }],
        };
        let id1 = compute_run_id(&system, &cert);
        let id2 = compute_run_id(&system, &cert);
        assert_eq!(id1, id2);
        assert!(id1.starts_with("run-"));
        assert_eq!(id1.len(), 4 + 16);

        let mut cert2 = cert.clone();
        cert2.interpretation[0].constant = 2;
        assert_ne!(compute_run_id(&system, &cert2), id1);
    }

    #[test]
    fn proof_record_json_roundtrip_preserves_signed_differences() {
        let mut differences = BTreeMap::new();
        differences.insert("x".to_string(), -3i128);
        let record = ProofRecord {
            run_id: "run-0000000000000001".to_string(),
            system: "t".to_string(),
            verdict: Verdict::Rejected,
            failures: vec![Failure::RuleNotDecreasing {
                rule: "r".to_string(),
                constant_difference: -2,
                negative_coefficients: vec!["x".to_string()],
            }],
            monotonicity: vec![],
            rules: vec![RuleProof {
                rule: "r".to_string(),
                lhs: LinearPoly::constant(1),
                rhs: LinearPoly::constant(3),
                coefficient_differences: differences,
                constant_difference: -2,
                decreases: false,
                reason: "constant difference -2 is not strictly positive".to_string(),
            }],
        };
        let json = serde_json::to_string(&record).unwrap();
        let back: ProofRecord = serde_json::from_str(&json).unwrap();
        assert_eq!(record, back);
        assert!(json.contains("\"rule_not_decreasing\""));
        assert!(json.contains("-3"));
    }
}
