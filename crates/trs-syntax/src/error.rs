//! Error contract shared by every crate in the workspace.
//!
//! Every failure carries an [`ErrorCategory`] so that callers (the CLI, test
//! harnesses, other tools) can distinguish *why* a run failed without parsing
//! human-readable text. The CLI maps categories to distinct exit codes.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Top-level classification of every failure the checker can report.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// Malformed input: unreadable files, invalid JSON, invalid systems or
    /// certificates (unknown symbols, arity mismatches, unbound variables...).
    Input,
    /// Conflicting state: a proof record does not match the inputs it claims
    /// to cover (produced by the independent verifier, never by `check`).
    StateConflict,
    /// A configured resource limit (term depth, node count, coefficient
    /// bound, symbol/rule count) was exceeded.
    ResourceExhausted,
    /// A computation failed, e.g. integer overflow while evaluating a
    /// linear polynomial.
    ComputationFailure,
}

/// Fine-grained, machine-matchable failure kinds.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    Io,
    Parse,
    InvalidName,
    DuplicateSymbol,
    UnknownSymbol,
    ArityMismatch,
    VariableLhs,
    UnboundVariable,
    DuplicateRule,
    CertificateSystemMismatch,
    CertificateMissingSymbol,
    CertificateUnknownSymbol,
    CertificateArityMismatch,
    TooManySymbols,
    TooManyRules,
    ArityLimit,
    TooManyNodes,
    TermTooDeep,
    CoefficientLimit,
    ArithmeticOverflow,
}

impl ErrorKind {
    pub fn category(&self) -> ErrorCategory {
        use ErrorKind::*;
        match self {
            Io | Parse | InvalidName | DuplicateSymbol | UnknownSymbol | ArityMismatch
            | VariableLhs | UnboundVariable | DuplicateRule | CertificateSystemMismatch
            | CertificateMissingSymbol | CertificateUnknownSymbol | CertificateArityMismatch => {
                ErrorCategory::Input
            }
            TooManySymbols | TooManyRules | ArityLimit | TooManyNodes | TermTooDeep
            | CoefficientLimit => ErrorCategory::ResourceExhausted,
            ArithmeticOverflow => ErrorCategory::ComputationFailure,
        }
    }
}

/// The single error type exchanged between all modules.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Error {
    pub category: ErrorCategory,
    pub kind: ErrorKind,
    pub message: String,
}

impl Error {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        let category = kind.category();
        Error {
            category,
            kind,
            message: message.into(),
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}/{:?}: {}", self.category, self.kind, self.message)
    }
}

impl std::error::Error for Error {}
