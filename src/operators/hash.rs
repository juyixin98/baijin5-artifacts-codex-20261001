//! Hash-based grouping executor.
//!
//! Strategy: key every row, insert into a `HashMap<GroupKey, Group>`. The map
//! hashes [`crate::collation::GroupKey`], whose `Hash`/`Eq` are defined over
//! the same `(rule, null, frags)` triple used by the sort executor's `Ord`,
//! so equal values necessarily collide into one bucket and unequal values
//! stay apart: the hash is *compatible* with sort equivalence. Output groups
//! are re-sorted by key so the result is byte-for-byte comparable to the sort
//! executor regardless of `HashMap`'s random state.

use std::collections::HashMap;

use crate::batch::InputBatch;
use crate::collation::{Collator, GroupKey};
use crate::error::Result;

use super::group::{ExecOutput, Group, KeyWarnings};
use super::keyed::key_rows;

/// Execute a hash-based group-by.
pub fn run(batch: &InputBatch, collator: &Collator) -> Result<ExecOutput> {
    let cols = batch.columns()?;
    let rows = key_rows(&cols, collator);

    // The map is the whole operator. `or_insert_with` guarantees the first
    // row in input order wins the representative, matching the sort path.
    let mut table: HashMap<GroupKey, Group> = HashMap::with_capacity(rows.len());
    let mut warnings = KeyWarnings::default();

    for r in rows {
        if r.overflow {
            warnings.numeric_overflow.push(r.record_id.clone());
        }
        let is_null = r.raw.is_none();
        let group = table.entry(r.key.clone()).or_insert_with(|| Group {
            representative: r.raw.clone().unwrap_or_default(),
            representative_record_id: r.record_id.clone(),
            member_record_ids: Vec::new(),
            count: 0,
            is_null,
        });
        group.count += 1;
        group.member_record_ids.push(r.record_id);
    }

    // Canonicalize order by the same key ordering the sort executor uses.
    let mut entries: Vec<(GroupKey, Group)> = table.into_iter().collect();
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    let groups = entries.into_iter().map(|(_, g)| g).collect();

    Ok(ExecOutput { groups, warnings })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::Row;
    use crate::collation::Rule;

    #[test]
    fn equivalent_values_share_a_bucket() -> Result<()> {
        let rows = vec![
            Row::new("r1", "Café"),
            Row::new("r2", "CAFE"),
            Row::new("r3", "cafe\u{0301}"),
            Row::new("r4", "tea"),
        ];
        let out = run(&InputBatch::from_rows(&rows)?, &Collator::new(Rule::v1()))?;
        assert_eq!(out.group_count(), 2);
        let cafe = out
            .groups
            .iter()
            .find(|g| g.count == 3)
            .expect("one merged cafe class");
        assert_eq!(cafe.representative, "Café");
        assert_eq!(cafe.representative_record_id, "r1");
        Ok(())
    }

    #[test]
    fn nulls_form_one_class() -> Result<()> {
        let rows = vec![
            Row::null_value("n1"),
            Row::new("x", "a"),
            Row::null_value("n2"),
        ];
        let out = run(&InputBatch::from_rows(&rows)?, &Collator::new(Rule::v1()))?;
        assert_eq!(out.group_count(), 2);
        let nulls = out.groups.iter().find(|g| g.is_null).unwrap();
        assert_eq!(nulls.count, 2);
        Ok(())
    }
}
