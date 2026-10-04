//! Resource control: budgets checked against the declared block header
//! *before* any payload byte is read or any output buffer is allocated.

use serde::Deserialize;

/// Limits applied to every block before decoding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct DecodeBudget {
    /// Maximum valid values a single block may declare.
    pub max_values_per_block: u64,
    /// Maximum payload bytes a single block may declare.
    pub max_payload_bytes: u64,
    /// Maximum runs a single RLE block may contain.
    pub max_runs_per_block: u64,
    /// Maximum total values produced by one `decode_column` call.
    pub max_total_values: u64,
}

impl Default for DecodeBudget {
    fn default() -> Self {
        DecodeBudget {
            max_values_per_block: 1_000_000,
            max_payload_bytes: 64 * 1024 * 1024,
            max_runs_per_block: 1_000_000,
            max_total_values: 16_000_000,
        }
    }
}
