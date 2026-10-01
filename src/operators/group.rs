//! Shared output of the grouping operators.

/// One equivalence class produced by an executor.
///
/// The [`Group::representative`] is an *original* string value (identity),
/// retained verbatim per the representative-value policy — it is not the
/// collation key. Which member becomes representative is deterministic and
/// identical between the sort and hash executors (first row in input order),
/// so the two strategies can be compared row-for-row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Group {
    /// Original raw value chosen as the class representative.
    pub representative: String,
    /// Identity of the representative row.
    pub representative_record_id: String,
    /// All member identities, in input (encounter) order.
    pub member_record_ids: Vec<String>,
    /// Number of rows in the class.
    pub count: usize,
    /// Whether this is the NULL class.
    pub is_null: bool,
}

/// Per-record undeterminable flags collected during keying.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct KeyWarnings {
    /// Record ids whose numeric token saturated `u64`.
    pub numeric_overflow: Vec<String>,
}

impl KeyWarnings {
    pub fn is_empty(&self) -> bool {
        self.numeric_overflow.is_empty()
    }
}

/// Executor result: groups in a stable order plus keying warnings.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExecOutput {
    pub groups: Vec<Group>,
    pub warnings: KeyWarnings,
}

impl ExecOutput {
    pub fn group_count(&self) -> usize {
        self.groups.len()
    }

    /// Distinct representative values — the dedup projection of the groups.
    pub fn distinct_values(&self) -> Vec<String> {
        self.groups
            .iter()
            .map(|g| g.representative.clone())
            .collect()
    }
}
