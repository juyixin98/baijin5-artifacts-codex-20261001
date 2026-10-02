//! Run model: raw sample records, symbol entries, run lifecycle state.
//!
//! A *run* is one offline capture session. Samples are ingested while the
//! run is `Open`; `Seal` freezes it. Every sample carries an explicit
//! `weight` — the number of original stacks it stands for after lossy
//! capture — so dropped-sample accounting is data, not convention.

use serde::{Deserialize, Serialize};

use crate::error::BackendError;

/// One frame as captured: a raw address plus an optional module hint.
/// Identity is derived from (module, address), never from a symbol name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FrameInput {
    pub addr: u64,
    #[serde(default)]
    pub module: Option<String>,
}

/// Async correlation metadata. A fragment stitches onto the sample owning
/// `parent_token` only when that token exists in the same run — the match
/// is the correlation evidence, recorded per sample in the audit output.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AsyncLink {
    pub task_id: u64,
    /// Token this sample publishes for children to attach to.
    #[serde(default)]
    pub token: Option<String>,
    /// Token of the parent fragment this sample continues from.
    #[serde(default)]
    pub parent_token: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SampleInput {
    pub sample_id: String,
    /// Number of original stacks this record represents. Must be finite, > 0.
    pub weight: f64,
    /// Root-first call stack.
    pub frames: Vec<FrameInput>,
    #[serde(default)]
    pub async_link: Option<AsyncLink>,
}

impl SampleInput {
    pub fn validate(&self) -> Result<(), BackendError> {
        if self.sample_id.trim().is_empty() {
            return Err(BackendError::input("empty_sample_id", "sample_id must be non-empty"));
        }
        if !self.weight.is_finite() || self.weight <= 0.0 {
            return Err(BackendError::input(
                "invalid_weight",
                format!("sample {}: weight must be finite and > 0, got {}", self.sample_id, self.weight),
            ));
        }
        if self.frames.is_empty() {
            return Err(BackendError::input(
                "empty_stack",
                format!("sample {}: stack must contain at least one frame", self.sample_id),
            ));
        }
        Ok(())
    }
}

/// Half-open address range [start, end) mapped to a symbol name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SymbolEntry {
    pub module: String,
    pub name: String,
    pub start: u64,
    pub end: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunState {
    Open,
    Sealed,
}

/// Persistent run metadata (stored as meta.json).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunMeta {
    pub run_id: String,
    pub state: RunState,
    pub created_unix: u64,
    /// Stacks the collector reports as lost (never seen by this backend).
    #[serde(default)]
    pub dropped_samples: u64,
    #[serde(default)]
    pub drop_reason: Option<String>,
    #[serde(default)]
    pub symbols: Vec<SymbolEntry>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(weight: f64, frames: Vec<FrameInput>) -> SampleInput {
        SampleInput { sample_id: "s".into(), weight, frames, async_link: None }
    }

    #[test]
    fn rejects_non_positive_and_non_finite_weights() {
        let f = vec![FrameInput { addr: 1, module: None }];
        for w in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            let err = sample(w, f.clone()).validate().unwrap_err();
            assert_eq!(err.category, crate::error::ErrorCategory::Input);
            assert_eq!(err.code, "invalid_weight");
        }
        assert!(sample(1.0, f).validate().is_ok());
    }

    #[test]
    fn rejects_empty_stack_and_blank_id() {
        let err = sample(1.0, vec![]).validate().unwrap_err();
        assert_eq!(err.code, "empty_stack");
        let mut s = sample(1.0, vec![FrameInput { addr: 1, module: None }]);
        s.sample_id = "  ".into();
        assert_eq!(s.validate().unwrap_err().code, "empty_sample_id");
    }
}
