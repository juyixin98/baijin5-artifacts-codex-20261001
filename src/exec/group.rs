//! The logical result of grouping: independent of which executor produced it.

use serde::{Deserialize, Serialize};

/// One equivalence class with concrete, assertable contents.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Group {
    /// Hex of the canonical sort-key bytes — the aggregation key (NOT an identity).
    pub key_hex: String,
    /// Rule version this key was computed under.
    pub rule_version: String,
    /// The surviving representative ORIGINAL value (per `RepresentativePolicy`).
    pub representative: String,
    /// Distinct original string identities that fell into the class, first-seen order.
    pub distinct_identities: Vec<String>,
    /// Number of input rows in the class.
    pub row_count: usize,
    /// Input row indices (0-based), first-seen order.
    pub rows: Vec<usize>,
    /// Bucket hash of the key; must be identical across both executors.
    pub hash: u64,
}

/// The full grouped result set.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GroupSet {
    pub column: String,
    pub rule_version: String,
    pub groups: Vec<Group>,
}

impl GroupSet {
    pub fn group_count(&self) -> usize {
        self.groups.len()
    }

    /// Total row count across groups (must equal the input batch length).
    pub fn total_rows(&self) -> usize {
        self.groups.iter().map(|g| g.row_count).sum()
    }

    /// Look up a group by representative (handy for tests/diagnostics).
    pub fn group_for_identity(&self, identity: &str) -> Option<&Group> {
        self.groups
            .iter()
            .find(|g| g.distinct_identities.iter().any(|i| i == identity))
    }
}
