//! Verdict reporting.
//!
//! The final report separates three things cleanly:
//! - [`Verdict`]: the overall conclusion (verified / rejected / unverified);
//! - [`Finding`] entries in `failures`: concrete, attributable reasons the
//!   proof is invalid (definite rejections);
//! - [`Uncertainty`] entries: reasons the checker could not complete an
//!   independent inspection (resource exhaustion, truncation, bad shape that
//!   prevents further trust).
//!
//! "Unverified" is never an acceptance: it expresses that the checker cannot
//! vouch for the proof.

use serde::Serialize;

/// Overall independent verdict.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    /// Every referenced step was checked and the root is the empty clause.
    Verified,
    /// A definite logical or structural invalidity was found.
    Rejected,
    /// Inspection could not be completed; the proof is not accepted.
    Unverified,
}

/// Definite failure categories. These assert the proof is invalid.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum FailureCategory {
    MalformedRecord,
    InvalidHeader,
    HeaderNotFirst,
    RecordAfterRoot,
    EmptyClauseNotTerminal,
    RootNotDeclared,
    RootNotEmpty,
    DuplicateNodeId,
    OutOfPhaseRecord,
    PivotNotPositive,
    ParentNotFound,
    IllegalPivotElimination,
}

/// Indeterminate categories. These prevent vouching without proving invalidity.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum UncertaintyCategory {
    ResourceExhausted,
    TruncatedStream,
}

#[derive(Debug, Clone, Serialize)]
pub struct Finding {
    pub category: FailureCategory,
    /// 1-based stream line, if attributable.
    pub line: Option<u64>,
    /// Related node/request-local identifier, if any.
    pub node: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Uncertainty {
    pub category: UncertaintyCategory,
    pub line: Option<u64>,
    pub message: String,
}

/// The machine-readable result document emitted on stdout.
#[derive(Debug, Clone, Serialize)]
pub struct Report {
    pub request_id: String,
    pub checker: String,
    pub version: String,
    pub format_version: Option<String>,
    pub verdict: Verdict,
    pub lines_processed: u64,
    pub inputs: usize,
    pub steps_checked: usize,
    pub root: Option<String>,
    /// Key checkpoints, in order (short human-readable step summaries).
    pub key_steps: Vec<String>,
    pub failures: Vec<Finding>,
    pub uncertainties: Vec<Uncertainty>,
}

impl Report {
    pub fn new(request_id: impl Into<String>) -> Self {
        Report {
            request_id: request_id.into(),
            checker: "resolution-checker".to_string(),
            version: env!("CARGO_PKG_VERSION").to_string(),
            format_version: None,
            verdict: Verdict::Unverified,
            lines_processed: 0,
            inputs: 0,
            steps_checked: 0,
            root: None,
            key_steps: Vec::new(),
            failures: Vec::new(),
            uncertainties: Vec::new(),
        }
    }

    pub fn fail(&mut self, f: Finding) {
        self.failures.push(f);
    }

    pub fn uncertain(&mut self, u: Uncertainty) {
        self.uncertainties.push(u);
    }

    pub fn has_failure(&self) -> bool {
        !self.failures.is_empty()
    }

    pub fn has_uncertainty(&self) -> bool {
        !self.uncertainties.is_empty()
    }

    /// Finalize the verdict using precedence:
    /// indeterminate > definite rejection > acceptance.
    pub fn finalize(&mut self) -> Verdict {
        self.verdict = if self.has_uncertainty() {
            Verdict::Unverified
        } else if self.has_failure() || self.root.is_none() {
            Verdict::Rejected
        } else {
            Verdict::Verified
        };
        self.verdict
    }
}
