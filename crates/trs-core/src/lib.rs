//! Inference core: checks linear-interpretation termination certificates
//! for term rewriting systems over the naturals and emits proof records.

#![forbid(unsafe_code)]

mod check;
mod eval;
mod interpret;

pub use check::check_certificate;

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use trs_proof::{Failure, LinearPoly, Verdict};
    use trs_syntax::{Certificate, ErrorCategory, ErrorKind, Limits, System};

    /// The running example: addition on unary naturals.
    ///   add(0, y)    -> y
    ///   add(s(x), y) -> s(add(x, y))
    const ADD_SYSTEM: &str = r#"{
        "name": "add-rec",
        "signature": [
            {"name": "0", "arity": 0},
            {"name": "s", "arity": 1},
            {"name": "add", "arity": 2}
        ],
        "rules": [
            {"name": "add_0",
             "lhs": {"fun": "add", "args": [{"fun": "0", "args": []}, {"var": "y"}]},
             "rhs": {"var": "y"}},
            {"name": "add_s",
             "lhs": {"fun": "add", "args": [{"fun": "s", "args": [{"var": "x"}]}, {"var": "y"}]},
             "rhs": {"fun": "s", "args": [{"fun": "add", "args": [{"var": "x"}, {"var": "y"}]}]}}
        ]
    }"#;

    fn add_cert(add_coeffs: [u64; 2], s_coeff: u64) -> Certificate {
        let json = format!(
            r#"{{"system": "add-rec", "interpretation": [
                {{"symbol": "0", "const": 1, "coeffs": []}},
                {{"symbol": "s", "const": 1, "coeffs": [{}]}},
                {{"symbol": "add", "const": 0, "coeffs": [{}, {}]}}
            ]}}"#,
            s_coeff, add_coeffs[0], add_coeffs[1]
        );
        serde_json::from_str(&json).unwrap()
    }

    fn poly(constant: u64, coeffs: &[(&str, u64)]) -> LinearPoly {
        LinearPoly {
            constant,
            coefficients: coeffs
                .iter()
                .map(|(v, c)| (v.to_string(), *c))
                .collect::<BTreeMap<_, _>>(),
        }
    }

    #[test]
    fn valid_certificate_terminates_with_hand_computed_polynomials() {
        // Hand-computed reference values (NOT produced by the checker):
        //   [0] = 1, [s](x) = 1 + x, [add](x,y) = 2x + y
        //   add_0: [lhs] = 2*1 + y = 2 + y,  [rhs] = y          => const diff 2
        //   add_s: [lhs] = 2(x+1) + y = 2 + 2x + y,
        //          [rhs] = 1 + (2x + y) = 1 + 2x + y           => const diff 1
        let system: System = serde_json::from_str(ADD_SYSTEM).unwrap();
        let cert = add_cert([2, 1], 1);
        let record = check_certificate(&system, &cert, &Limits::default()).unwrap();

        assert_eq!(record.verdict, Verdict::Terminating);
        assert!(record.failures.is_empty());
        assert!(record.monotonicity.iter().all(|m| m.strict));
        assert!(record.run_id.starts_with("run-"));

        let add_0 = &record.rules[0];
        assert_eq!(add_0.rule, "add_0");
        assert_eq!(add_0.lhs, poly(2, &[("y", 1)]));
        assert_eq!(add_0.rhs, poly(0, &[("y", 1)]));
        assert_eq!(add_0.constant_difference, 2);
        assert_eq!(add_0.coefficient_differences, {
            let mut m = BTreeMap::new();
            m.insert("y".to_string(), 0i128);
            m
        });
        assert!(add_0.decreases);

        let add_s = &record.rules[1];
        assert_eq!(add_s.rule, "add_s");
        assert_eq!(add_s.lhs, poly(2, &[("x", 2), ("y", 1)]));
        assert_eq!(add_s.rhs, poly(1, &[("x", 2), ("y", 1)]));
        assert_eq!(add_s.constant_difference, 1);
        assert!(add_s.decreases);
        assert!(add_s.reason.contains("1 > 0"));
    }

    #[test]
    fn tampered_coefficient_is_rejected_with_rule_failure() {
        // Tampering: [add](x,y) = x + y (coefficient 2 -> 1).
        //   add_s: [lhs] = (x+1) + y = 1 + x + y, [rhs] = 1 + x + y => diff 0.
        let system: System = serde_json::from_str(ADD_SYSTEM).unwrap();
        let cert = add_cert([1, 1], 1);
        let record = check_certificate(&system, &cert, &Limits::default()).unwrap();

        assert_eq!(record.verdict, Verdict::Rejected);
        assert!(record.rules[0].decreases, "add_0 still decreases");
        assert!(!record.rules[1].decreases, "add_s no longer decreases");
        assert_eq!(record.rules[1].constant_difference, 0);
        assert_eq!(
            record.failures,
            vec![Failure::RuleNotDecreasing {
                rule: "add_s".to_string(),
                constant_difference: 0,
                negative_coefficients: vec![],
            }]
        );
    }

    #[test]
    fn zero_argument_coefficient_violates_strict_monotonicity() {
        // Tampering: [s](x) = 1 + 0*x. Rules still happen to decrease, but the
        // interpretation is not strictly monotone, so it proves nothing.
        let system: System = serde_json::from_str(ADD_SYSTEM).unwrap();
        let cert = add_cert([2, 1], 0);
        let record = check_certificate(&system, &cert, &Limits::default()).unwrap();

        assert_eq!(record.verdict, Verdict::Rejected);
        assert!(
            record.rules.iter().all(|r| r.decreases),
            "rules decrease even under the tampered interpretation"
        );
        assert_eq!(
            record.failures,
            vec![Failure::MonotonicityViolation {
                symbol: "s".to_string(),
                argument: 0,
                coefficient: 0,
            }]
        );
        let s_entry = record
            .monotonicity
            .iter()
            .find(|m| m.symbol == "s")
            .unwrap();
        assert!(!s_entry.strict);
        assert_eq!(s_entry.violating_arguments, vec![0]);
    }

    #[test]
    fn multiplication_has_no_linear_certificate() {
        // mul(0, y) -> 0 ; mul(s(x), y) -> add(mul(x, y), y)
        // Hand-computed: with [mul](x,y) = x + y the first rule gives
        // const diff 0 and the second gives negative coefficient differences.
        let system: System = serde_json::from_str(
            r#"{
                "name": "mul-rec",
                "signature": [
                    {"name": "0", "arity": 0},
                    {"name": "s", "arity": 1},
                    {"name": "add", "arity": 2},
                    {"name": "mul", "arity": 2}
                ],
                "rules": [
                    {"name": "mul_0",
                     "lhs": {"fun": "mul", "args": [{"fun": "0", "args": []}, {"var": "y"}]},
                     "rhs": {"fun": "0", "args": []}},
                    {"name": "mul_s",
                     "lhs": {"fun": "mul", "args": [{"fun": "s", "args": [{"var": "x"}]}, {"var": "y"}]},
                     "rhs": {"fun": "add", "args": [
                        {"fun": "mul", "args": [{"var": "x"}, {"var": "y"}]}, {"var": "y"}]}}
                ]
            }"#,
        )
        .unwrap();
        let cert: Certificate = serde_json::from_str(
            r#"{"system": "mul-rec", "interpretation": [
                {"symbol": "0", "const": 1, "coeffs": []},
                {"symbol": "s", "const": 1, "coeffs": [1]},
                {"symbol": "add", "const": 0, "coeffs": [2, 1]},
                {"symbol": "mul", "const": 0, "coeffs": [1, 1]}
            ]}"#,
        )
        .unwrap();
        let record = check_certificate(&system, &cert, &Limits::default()).unwrap();

        assert_eq!(record.verdict, Verdict::Rejected);
        // mul_0: [lhs] = 1 + y, [rhs] = 1 => const diff 0.
        assert_eq!(record.rules[0].lhs, poly(1, &[("y", 1)]));
        assert_eq!(record.rules[0].rhs, poly(1, &[]));
        assert_eq!(record.rules[0].constant_difference, 0);
        // mul_s: [lhs] = 1 + x + y, [rhs] = 2x + 3y => x: -1, y: -2.
        assert_eq!(record.rules[1].lhs, poly(1, &[("x", 1), ("y", 1)]));
        assert_eq!(record.rules[1].rhs, poly(0, &[("x", 2), ("y", 3)]));
        assert_eq!(record.rules[1].coefficient_differences["x"], -1);
        assert_eq!(record.rules[1].coefficient_differences["y"], -2);
        assert_eq!(
            record.failures,
            vec![
                Failure::RuleNotDecreasing {
                    rule: "mul_0".to_string(),
                    constant_difference: 0,
                    negative_coefficients: vec![],
                },
                Failure::RuleNotDecreasing {
                    rule: "mul_s".to_string(),
                    constant_difference: 1,
                    negative_coefficients: vec!["x".to_string(), "y".to_string()],
                },
            ]
        );
    }

    #[test]
    fn empty_system_trivially_terminates() {
        let system: System =
            serde_json::from_str(r#"{"name": "empty", "signature": [], "rules": []}"#).unwrap();
        let cert: Certificate =
            serde_json::from_str(r#"{"system": "empty", "interpretation": []}"#).unwrap();
        let record = check_certificate(&system, &cert, &Limits::default()).unwrap();
        assert_eq!(record.verdict, Verdict::Terminating);
        assert!(record.rules.is_empty());
    }

    #[test]
    fn arithmetic_overflow_is_computation_failure() {
        // [a] = u64::MAX, [f](x) = 2x: evaluating f(a) computes 2 * u64::MAX.
        let system: System = serde_json::from_str(
            r#"{
                "name": "ovf",
                "signature": [{"name": "a", "arity": 0}, {"name": "f", "arity": 1}],
                "rules": [{"name": "r",
                    "lhs": {"fun": "f", "args": [{"fun": "a", "args": []}]},
                    "rhs": {"fun": "a", "args": []}}]
            }"#,
        )
        .unwrap();
        let cert: Certificate = serde_json::from_str(
            r#"{"system": "ovf", "interpretation": [
                {"symbol": "a", "const": 18446744073709551615, "coeffs": []},
                {"symbol": "f", "const": 0, "coeffs": [2]}
            ]}"#,
        )
        .unwrap();
        let limits = Limits {
            max_coefficient: u64::MAX,
            ..Limits::default()
        };
        let err = check_certificate(&system, &cert, &limits).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::ArithmeticOverflow);
        assert_eq!(err.category, ErrorCategory::ComputationFailure);
    }

    #[test]
    fn coefficient_bound_violation_is_resource_exhaustion() {
        // [a] = 2, [f](x) = 2x: evaluating f(a) yields 4 > limit 3.
        let system: System = serde_json::from_str(
            r#"{
                "name": "big",
                "signature": [{"name": "a", "arity": 0}, {"name": "f", "arity": 1}],
                "rules": [{"name": "r",
                    "lhs": {"fun": "f", "args": [{"fun": "a", "args": []}]},
                    "rhs": {"fun": "a", "args": []}}]
            }"#,
        )
        .unwrap();
        let cert: Certificate = serde_json::from_str(
            r#"{"system": "big", "interpretation": [
                {"symbol": "a", "const": 2, "coeffs": []},
                {"symbol": "f", "const": 0, "coeffs": [2]}
            ]}"#,
        )
        .unwrap();
        let limits = Limits {
            max_coefficient: 3,
            ..Limits::default()
        };
        let err = check_certificate(&system, &cert, &limits).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::CoefficientLimit);
        assert_eq!(err.category, ErrorCategory::ResourceExhausted);
    }

    #[test]
    fn term_depth_limit_is_resource_exhaustion() {
        let system: System = serde_json::from_str(ADD_SYSTEM).unwrap();
        let cert = add_cert([2, 1], 1);
        let limits = Limits {
            max_term_depth: 1,
            ..Limits::default()
        };
        let err = check_certificate(&system, &cert, &limits).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::TermTooDeep);
        assert_eq!(err.category, ErrorCategory::ResourceExhausted);
    }

    #[test]
    fn certificate_for_wrong_system_is_input_error() {
        let system: System = serde_json::from_str(ADD_SYSTEM).unwrap();
        let mut cert = add_cert([2, 1], 1);
        cert.system = "somebody-else".to_string();
        let err = check_certificate(&system, &cert, &Limits::default()).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::CertificateSystemMismatch);
        assert_eq!(err.category, ErrorCategory::Input);
    }
}
