//! Data model: signatures, terms, rules, systems and certificates.
//!
//! JSON shapes (serde):
//! * term:      `{"var": "x"}` or `{"fun": "s", "args": [...]}` (`args` may be
//!   omitted for constants)
//! * rule:      `{"name": "r1", "lhs": <term>, "rhs": <term>}`
//! * system:    `{"name": ..., "signature": [{"name","arity"}], "rules": [...]}`
//! * certificate: `{"system": <system name>, "interpretation":
//!   [{"symbol": "f", "const": 1, "coeffs": [2, 1]}]}` where `coeffs[i]` is the
//!   coefficient of the (i+1)-th argument, so `[f](x1..xn) = const + sum
//!   coeffs[i]*x(i+1)`.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SymbolDecl {
    pub name: String,
    pub arity: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Term {
    Var {
        var: String,
    },
    Fun {
        fun: String,
        #[serde(default)]
        args: Vec<Term>,
    },
}

impl Term {
    pub fn var(name: &str) -> Term {
        Term::Var {
            var: name.to_string(),
        }
    }

    pub fn fun(name: &str, args: Vec<Term>) -> Term {
        Term::Fun {
            fun: name.to_string(),
            args,
        }
    }

    /// All variables occurring in the term (sorted, deduplicated).
    pub fn variables(&self) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        self.collect_vars(&mut out);
        out
    }

    pub fn collect_vars(&self, out: &mut BTreeSet<String>) {
        match self {
            Term::Var { var } => {
                out.insert(var.clone());
            }
            Term::Fun { args, .. } => {
                for arg in args {
                    arg.collect_vars(out);
                }
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Rule {
    pub name: String,
    pub lhs: Term,
    pub rhs: Term,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct System {
    pub name: String,
    pub signature: Vec<SymbolDecl>,
    #[serde(default)]
    pub rules: Vec<Rule>,
}

/// Interpretation of one symbol: `[f](x1..xn) = const + sum_i coeffs[i] * x(i+1)`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SymbolInterpretation {
    pub symbol: String,
    #[serde(rename = "const")]
    pub constant: u64,
    #[serde(default)]
    pub coeffs: Vec<u64>,
}

/// A termination certificate: one linear interpretation per signature symbol.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Certificate {
    /// Name of the system this certificate claims to prove terminating.
    pub system: String,
    pub interpretation: Vec<SymbolInterpretation>,
}
