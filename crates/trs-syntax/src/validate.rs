//! Validation of systems and certificates against the syntactic contract:
//! symbol arity, variable scoping, and structural certificate shape.
//!
//! Note what is *not* checked here: strict monotonicity of interpretation
//! coefficients and rule decrease are properties of the certificate as a
//! termination proof, so they are judged by the inference core (and the
//! independent verifier) and recorded in the proof record, not treated as
//! input errors.

use crate::error::{Error, ErrorKind};
use crate::limits::Limits;
use crate::model::{Certificate, System, Term};
use std::collections::{BTreeSet, HashMap, HashSet};

/// A validated signature: symbol name -> arity, plus declaration order.
#[derive(Debug, Clone)]
pub struct Signature {
    arities: HashMap<String, usize>,
    order: Vec<String>,
}

impl Signature {
    pub fn arity(&self, name: &str) -> Option<usize> {
        self.arities.get(name).copied()
    }

    /// Symbol names in declaration order.
    pub fn symbols(&self) -> &[String] {
        &self.order
    }

    pub fn len(&self) -> usize {
        self.order.len()
    }

    pub fn is_empty(&self) -> bool {
        self.order.is_empty()
    }
}

/// Validate a rewriting system and return its signature for further use.
///
/// Checks: name/arity well-formedness, unique symbols and rule names, terms
/// built from known symbols with matching arity, left-hand sides are not
/// variables, and every right-hand-side variable is bound on the left.
pub fn validate_system(system: &System, limits: &Limits) -> Result<Signature, Error> {
    if system.signature.len() > limits.max_symbols {
        return Err(Error::new(
            ErrorKind::TooManySymbols,
            format!(
                "system '{}' declares {} symbols, limit is {}",
                system.name,
                system.signature.len(),
                limits.max_symbols
            ),
        ));
    }
    if system.rules.len() > limits.max_rules {
        return Err(Error::new(
            ErrorKind::TooManyRules,
            format!(
                "system '{}' declares {} rules, limit is {}",
                system.name,
                system.rules.len(),
                limits.max_rules
            ),
        ));
    }

    let mut arities = HashMap::new();
    let mut order = Vec::new();
    for decl in &system.signature {
        if decl.name.is_empty() {
            return Err(Error::new(
                ErrorKind::InvalidName,
                "symbol name must not be empty".to_string(),
            ));
        }
        if decl.arity > limits.max_arity {
            return Err(Error::new(
                ErrorKind::ArityLimit,
                format!(
                    "symbol '{}' has arity {}, limit is {}",
                    decl.name, decl.arity, limits.max_arity
                ),
            ));
        }
        if arities.insert(decl.name.clone(), decl.arity).is_some() {
            return Err(Error::new(
                ErrorKind::DuplicateSymbol,
                format!("symbol '{}' is declared twice", decl.name),
            ));
        }
        order.push(decl.name.clone());
    }
    let signature = Signature { arities, order };

    let mut rule_names = HashSet::new();
    for rule in &system.rules {
        if rule.name.is_empty() {
            return Err(Error::new(
                ErrorKind::InvalidName,
                "rule name must not be empty".to_string(),
            ));
        }
        if !rule_names.insert(rule.name.clone()) {
            return Err(Error::new(
                ErrorKind::DuplicateRule,
                format!("rule name '{}' is used twice", rule.name),
            ));
        }
        if matches!(rule.lhs, Term::Var { .. }) {
            return Err(Error::new(
                ErrorKind::VariableLhs,
                format!("rule '{}': left-hand side is a variable", rule.name),
            ));
        }
        validate_term(&rule.lhs, &signature, limits, 1, &mut 0)?;
        validate_term(&rule.rhs, &signature, limits, 1, &mut 0)?;

        let lhs_vars = rule.lhs.variables();
        for var in rule.rhs.variables().difference(&lhs_vars) {
            return Err(Error::new(
                ErrorKind::UnboundVariable,
                format!(
                    "rule '{}': variable '{}' occurs on the right-hand side but is not bound on the left-hand side",
                    rule.name, var
                ),
            ));
        }
    }
    Ok(signature)
}

fn validate_term(
    term: &Term,
    signature: &Signature,
    limits: &Limits,
    depth: usize,
    nodes: &mut usize,
) -> Result<(), Error> {
    if depth > limits.max_term_depth {
        return Err(Error::new(
            ErrorKind::TermTooDeep,
            format!(
                "term nesting depth exceeds limit of {}",
                limits.max_term_depth
            ),
        ));
    }
    *nodes += 1;
    if *nodes > limits.max_term_nodes {
        return Err(Error::new(
            ErrorKind::TooManyNodes,
            format!("term has more than {} nodes", limits.max_term_nodes),
        ));
    }
    match term {
        Term::Var { var } => {
            if var.is_empty() {
                return Err(Error::new(
                    ErrorKind::InvalidName,
                    "variable name must not be empty".to_string(),
                ));
            }
            Ok(())
        }
        Term::Fun { fun, args } => {
            let arity = signature.arity(fun).ok_or_else(|| {
                Error::new(
                    ErrorKind::UnknownSymbol,
                    format!("unknown symbol '{}' in term", fun),
                )
            })?;
            if args.len() != arity {
                return Err(Error::new(
                    ErrorKind::ArityMismatch,
                    format!(
                        "symbol '{}' expects {} argument(s) but is applied to {}",
                        fun,
                        arity,
                        args.len()
                    ),
                ));
            }
            for arg in args {
                validate_term(arg, signature, limits, depth + 1, nodes)?;
            }
            Ok(())
        }
    }
}

/// Validate the *structural* shape of a certificate against a validated
/// system: it must target this system and interpret every signature symbol
/// exactly once, with `arity` many argument coefficients, all within the
/// configured coefficient bound.
///
/// Strict monotonicity (every argument coefficient >= 1) is deliberately NOT
/// enforced here; it is a judgment of the checker, recorded in the proof.
pub fn validate_certificate(
    cert: &Certificate,
    system: &System,
    signature: &Signature,
    limits: &Limits,
) -> Result<(), Error> {
    if cert.system != system.name {
        return Err(Error::new(
            ErrorKind::CertificateSystemMismatch,
            format!(
                "certificate is for system '{}' but the input system is '{}'",
                cert.system, system.name
            ),
        ));
    }

    let mut seen: BTreeSet<&str> = BTreeSet::new();
    for entry in &cert.interpretation {
        let arity = signature.arity(&entry.symbol).ok_or_else(|| {
            Error::new(
                ErrorKind::CertificateUnknownSymbol,
                format!(
                    "certificate interprets '{}' which is not in the signature",
                    entry.symbol
                ),
            )
        })?;
        if !seen.insert(entry.symbol.as_str()) {
            return Err(Error::new(
                ErrorKind::DuplicateSymbol,
                format!(
                    "certificate interprets symbol '{}' more than once",
                    entry.symbol
                ),
            ));
        }
        if entry.coeffs.len() != arity {
            return Err(Error::new(
                ErrorKind::CertificateArityMismatch,
                format!(
                    "symbol '{}' has arity {} but the certificate gives {} coefficient(s)",
                    entry.symbol,
                    arity,
                    entry.coeffs.len()
                ),
            ));
        }
        if entry.constant > limits.max_coefficient {
            return Err(Error::new(
                ErrorKind::CoefficientLimit,
                format!(
                    "constant {} for symbol '{}' exceeds the coefficient limit {}",
                    entry.constant, entry.symbol, limits.max_coefficient
                ),
            ));
        }
        for (index, coeff) in entry.coeffs.iter().enumerate() {
            if *coeff > limits.max_coefficient {
                return Err(Error::new(
                    ErrorKind::CoefficientLimit,
                    format!(
                        "coefficient {} of argument {} of symbol '{}' exceeds the coefficient limit {}",
                        coeff,
                        index + 1,
                        entry.symbol,
                        limits.max_coefficient
                    ),
                ));
            }
        }
    }

    for symbol in signature.symbols() {
        if !seen.contains(symbol.as_str()) {
            return Err(Error::new(
                ErrorKind::CertificateMissingSymbol,
                format!("certificate does not interpret symbol '{}'", symbol),
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Rule, SymbolDecl, SymbolInterpretation};

    fn system(signature: Vec<(&str, usize)>, rules: Vec<Rule>) -> System {
        System {
            name: "t".to_string(),
            signature: signature
                .into_iter()
                .map(|(name, arity)| SymbolDecl {
                    name: name.to_string(),
                    arity,
                })
                .collect(),
            rules,
        }
    }

    fn rule(name: &str, lhs: Term, rhs: Term) -> Rule {
        Rule {
            name: name.to_string(),
            lhs,
            rhs,
        }
    }

    fn kind_of(result: Result<Signature, Error>) -> ErrorKind {
        result.expect_err("expected validation to fail").kind
    }

    #[test]
    fn valid_system_passes() {
        let sys = system(
            vec![("0", 0), ("s", 1)],
            vec![rule(
                "r1",
                Term::fun("s", vec![Term::var("x")]),
                Term::var("x"),
            )],
        );
        let sig = validate_system(&sys, &Limits::default()).expect("valid system");
        assert_eq!(sig.arity("s"), Some(1));
        assert_eq!(sig.arity("0"), Some(0));
        assert_eq!(sig.symbols(), &["0".to_string(), "s".to_string()]);
    }

    #[test]
    fn duplicate_symbol_rejected() {
        let sys = system(vec![("f", 1), ("f", 2)], vec![]);
        assert_eq!(
            kind_of(validate_system(&sys, &Limits::default())),
            ErrorKind::DuplicateSymbol
        );
    }

    #[test]
    fn empty_symbol_name_rejected() {
        let sys = system(vec![("", 0)], vec![]);
        assert_eq!(
            kind_of(validate_system(&sys, &Limits::default())),
            ErrorKind::InvalidName
        );
    }

    #[test]
    fn unknown_symbol_in_term_rejected() {
        let sys = system(
            vec![("f", 1)],
            vec![rule("r", Term::fun("g", vec![Term::var("x")]), Term::var("x"))],
        );
        assert_eq!(
            kind_of(validate_system(&sys, &Limits::default())),
            ErrorKind::UnknownSymbol
        );
    }

    #[test]
    fn arity_mismatch_rejected() {
        let sys = system(
            vec![("s", 1)],
            vec![rule(
                "r",
                Term::fun("s", vec![Term::var("x"), Term::var("y")]),
                Term::var("x"),
            )],
        );
        let err = validate_system(&sys, &Limits::default()).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::ArityMismatch);
        assert!(err.message.contains("expects 1 argument(s)"));
    }

    #[test]
    fn unbound_rhs_variable_rejected() {
        let sys = system(
            vec![("f", 1), ("g", 1)],
            vec![rule(
                "r",
                Term::fun("f", vec![Term::var("x")]),
                Term::fun("g", vec![Term::var("y")]),
            )],
        );
        let err = validate_system(&sys, &Limits::default()).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::UnboundVariable);
        assert!(err.message.contains("'y'"));
    }

    #[test]
    fn variable_lhs_rejected() {
        let sys = system(
            vec![("f", 1)],
            vec![rule("r", Term::var("x"), Term::fun("f", vec![Term::var("x")]))],
        );
        assert_eq!(
            kind_of(validate_system(&sys, &Limits::default())),
            ErrorKind::VariableLhs
        );
    }

    #[test]
    fn duplicate_rule_name_rejected() {
        let sys = system(
            vec![("f", 1)],
            vec![
                rule("r", Term::fun("f", vec![Term::var("x")]), Term::var("x")),
                rule("r", Term::fun("f", vec![Term::var("y")]), Term::var("y")),
            ],
        );
        assert_eq!(
            kind_of(validate_system(&sys, &Limits::default())),
            ErrorKind::DuplicateRule
        );
    }

    #[test]
    fn term_depth_limit_is_resource_error() {
        let sys = system(
            vec![("f", 1)],
            vec![rule(
                "r",
                Term::fun("f", vec![Term::fun("f", vec![Term::var("x")])]),
                Term::var("x"),
            )],
        );
        let limits = Limits {
            max_term_depth: 2,
            ..Limits::default()
        };
        let err = validate_system(&sys, &limits).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::TermTooDeep);
        assert_eq!(err.category, crate::ErrorCategory::ResourceExhausted);
    }

    #[test]
    fn symbol_count_limit_is_resource_error() {
        let sys = system(vec![("a", 0), ("b", 0)], vec![]);
        let limits = Limits {
            max_symbols: 1,
            ..Limits::default()
        };
        let err = validate_system(&sys, &limits).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::TooManySymbols);
        assert_eq!(err.category, crate::ErrorCategory::ResourceExhausted);
    }

    fn cert_for(name: &str, entries: Vec<(&str, u64, Vec<u64>)>) -> Certificate {
        Certificate {
            system: name.to_string(),
            interpretation: entries
                .into_iter()
                .map(|(symbol, constant, coeffs)| SymbolInterpretation {
                    symbol: symbol.to_string(),
                    constant,
                    coeffs,
                })
                .collect(),
        }
    }

    #[test]
    fn certificate_system_name_mismatch() {
        let sys = system(vec![("f", 1)], vec![]);
        let sig = validate_system(&sys, &Limits::default()).unwrap();
        let cert = cert_for("other", vec![("f", 0, vec![1])]);
        assert_eq!(
            validate_certificate(&cert, &sys, &sig, &Limits::default())
                .expect_err("must fail")
                .kind,
            ErrorKind::CertificateSystemMismatch
        );
    }

    #[test]
    fn certificate_missing_symbol() {
        let sys = system(vec![("f", 1), ("g", 1)], vec![]);
        let sig = validate_system(&sys, &Limits::default()).unwrap();
        let cert = cert_for("t", vec![("f", 0, vec![1])]);
        assert_eq!(
            validate_certificate(&cert, &sys, &sig, &Limits::default())
                .expect_err("must fail")
                .kind,
            ErrorKind::CertificateMissingSymbol
        );
    }

    #[test]
    fn certificate_unknown_symbol() {
        let sys = system(vec![("f", 1)], vec![]);
        let sig = validate_system(&sys, &Limits::default()).unwrap();
        let cert = cert_for("t", vec![("f", 0, vec![1]), ("h", 0, vec![1])]);
        assert_eq!(
            validate_certificate(&cert, &sys, &sig, &Limits::default())
                .expect_err("must fail")
                .kind,
            ErrorKind::CertificateUnknownSymbol
        );
    }

    #[test]
    fn certificate_arity_mismatch() {
        let sys = system(vec![("f", 2)], vec![]);
        let sig = validate_system(&sys, &Limits::default()).unwrap();
        let cert = cert_for("t", vec![("f", 0, vec![1])]);
        assert_eq!(
            validate_certificate(&cert, &sys, &sig, &Limits::default())
                .expect_err("must fail")
                .kind,
            ErrorKind::CertificateArityMismatch
        );
    }

    #[test]
    fn certificate_coefficient_limit_is_resource_error() {
        let sys = system(vec![("f", 1)], vec![]);
        let sig = validate_system(&sys, &Limits::default()).unwrap();
        let limits = Limits {
            max_coefficient: 3,
            ..Limits::default()
        };
        let cert = cert_for("t", vec![("f", 0, vec![4])]);
        let err = validate_certificate(&cert, &sys, &sig, &limits).expect_err("must fail");
        assert_eq!(err.kind, ErrorKind::CoefficientLimit);
        assert_eq!(err.category, crate::ErrorCategory::ResourceExhausted);
    }
}
