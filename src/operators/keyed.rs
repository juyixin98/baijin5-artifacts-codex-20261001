//! Per-row keyed intermediate shared by both executors.

use crate::batch::TypedColumns;
use crate::collation::{Collator, GroupKey};

/// One row after keying, still carrying its original identity.
pub(crate) struct RowKey {
    pub row_index: usize,
    pub record_id: String,
    /// Original raw value (`None` for SQL NULL).
    pub raw: Option<String>,
    pub key: GroupKey,
    pub overflow: bool,
}

/// Key every row of a typed column batch. The collator version is embedded in
/// every key, so a batch keyed under one rule can never mix with another.
pub(crate) fn key_rows(cols: &TypedColumns<'_>, collator: &Collator) -> Vec<RowKey> {
    cols.iter()
        .enumerate()
        .map(|(row_index, (record_id, value))| {
            let (key, overflow) = match value {
                None => (GroupKey::null_key(collator.version()), false),
                Some(v) => {
                    let k = collator.key_of(v);
                    (k.key, k.overflow)
                }
            };
            RowKey {
                row_index,
                record_id: record_id.to_string(),
                raw: value.map(str::to_string),
                key,
                overflow,
            }
        })
        .collect()
}
