//! Proof record: the replayable evidence produced by checking a certificate.
//!
//! A record contains everything needed to replay and independently audit a
//! run: the run id, the verdict, the interpreted monotonicity table, and for
//! every rule the evaluated linear polynomials of both sides, their
//! coefficient-wise differences, and the human-readable reason for the
//! per-rule judgment.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fmt;

/// A linear polynomial over natural-number variables:
/// `constant + sum_v coefficients[v] * v`. Zero coefficients are never stored.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LinearPoly {
    #[serde(rename = "const")]
    pub constant: u64,
    #[serde(default)]
    pub coefficients: BTreeMap<String, u64>,
}

impl LinearPoly {
    pub fn constant(value: u64) -> Self {
        LinearPoly {
            constant: value,
            coefficients: BTreeMap::new(),
        }
    }

    pub fn variable(name: &str) -> Self {
        let mut coefficients = BTreeMap::new();
        coefficients.insert(name.to_string(), 1);
        LinearPoly {
            constant: 0,
            coefficients,
        }
    }

    pub fn coefficient_of(&self, var: &str) -> u64 {
        self.coefficients.get(var).copied().unwrap_or(0)
    }
}

impl fmt::Display for LinearPoly {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.constant)?;
        for (var, coeff) in &self.coefficients {
            if *coeff == 1 {
                write!(f, " + {}", var)?;
            } else {
                write!(f, " + {}*{}", coeff, var)?;
            }
        }
        Ok(())
    }
}

/// The only two judgments this tool ever produces.
///
/// There is deliberately NO "non-terminating" verdict: the checker proves
/// termination via a sufficient condition (linear interpretations). Failure
/// to validate a certificate means exactly that — the certificate is
/// rejected — and never a claim about non-termination.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    /// Every rule strictly decreases under the interpretation and the
    /// interpretation is strictly monotone: the system terminates.
    Terminating,
    /// The certificate does not satisfy the sufficient condition.
    Rejected,
}

impl fmt::Display for Verdict {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Verdict::Terminating => write!(f, "terminating"),
            Verdict::Rejected => write!(f, "rejected"),
        }
    }
}

/// One reason a certificate was rejected.
///
/// Serialized as an externally tagged enum, e.g.
/// `{"rule_not_decreasing": {"rule": "r1", ...}}` — the variant name is the
/// machine-matchable discriminator. (Internally tagged enums cannot carry
/// `i128` fields through serde's content buffering.)
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Failure {
    /// `[symbol]` is not strictly monotone in argument `argument` (0-based):
    /// its coefficient is 0.
    MonotonicityViolation {
        symbol: String,
        argument: usize,
        coefficient: u64,
    },
    /// A rule does not strictly decrease: either the constant difference is
    /// not strictly positive, or some variable coefficient decreases.
    RuleNotDecreasing {
        rule: String,
        constant_difference: i128,
        negative_coefficients: Vec<String>,
    },
}

/// Monotonicity evidence for one interpreted symbol.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MonotonicityEntry {
    pub symbol: String,
    #[serde(rename = "const")]
    pub constant: u64,
    pub coefficients: Vec<u64>,
    /// 0-based argument indices whose coefficient is 0 (empty iff strict).
    pub violating_arguments: Vec<usize>,
    pub strict: bool,
}

/// Per-rule evidence: evaluated polynomials and the decrease judgment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuleProof {
    pub rule: String,
    pub lhs: LinearPoly,
    pub rhs: LinearPoly,
    /// `lhs - rhs` per variable (union of both sides' variables).
    pub coefficient_differences: BTreeMap<String, i128>,
    /// `lhs.const - rhs.const`.
    pub constant_difference: i128,
    pub decreases: bool,
    /// Human-readable justification of the judgment.
    pub reason: String,
}

/// The full, serializable evidence of one checking run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProofRecord {
    pub run_id: String,
    pub system: String,
    pub verdict: Verdict,
    pub failures: Vec<Failure>,
    pub monotonicity: Vec<MonotonicityEntry>,
    pub rules: Vec<RuleProof>,
}
