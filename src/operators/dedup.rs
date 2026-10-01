//! Dedup executor.
//!
//! Distinct from grouping: dedup emits one *original* row per equivalence
//! class, in original input order, using the representative-value policy
//! (first member wins). It shares the exact keying/equivalence semantics, so
//! `dedup` is the row-preserving projection of `group`.

use std::collections::HashSet;

use crate::batch::{InputBatch, Row};
use crate::collation::{Collator, GroupKey};
use crate::error::Result;

use super::keyed::key_rows;

/// One retained row after dedup.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DedupRow {
    pub record_id: String,
    /// The verbatim original value (`None` if NULL).
    pub value: Option<String>,
}

/// Deduplicate while preserving first input order and raw representative
/// values.
pub fn run(batch: &InputBatch, collator: &Collator) -> Result<Vec<DedupRow>> {
    let cols = batch.columns()?;
    let rows = key_rows(&cols, collator);

    let mut seen: HashSet<GroupKey> = HashSet::with_capacity(rows.len());
    let mut out = Vec::new();
    for r in rows {
        if seen.insert(r.key) {
            out.push(DedupRow {
                record_id: r.record_id,
                value: r.raw,
            });
        }
    }
    Ok(out)
}

/// Convenience: dedup plain rows without an explicit batch.
pub fn dedup_rows(rows: &[Row], collator: &Collator) -> Result<Vec<DedupRow>> {
    run(&InputBatch::from_rows(rows)?, collator)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collation::Rule;

    #[test]
    fn keeps_first_original_value_in_order() -> Result<()> {
        let rows = vec![
            Row::new("r1", "CAFE"),
            Row::new("r2", "café"),
            Row::new("r3", "tea"),
            Row::new("r4", "Café"),
        ];
        let out = dedup_rows(&rows, &Collator::new(Rule::v1()))?;
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].record_id, "r1");
        assert_eq!(out[0].value.as_deref(), Some("CAFE")); // raw, not key
        assert_eq!(out[1].value.as_deref(), Some("tea"));
        Ok(())
    }
}
