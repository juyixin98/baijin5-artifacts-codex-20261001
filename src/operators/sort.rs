//! Sort-based grouping executor.
//!
//! Strategy: key every row, stably sort by [`GroupKey`] (ties keep input
//! order), then sweep adjacent equal keys into groups. Because equality and
//! ordering are defined by the same `Ord`/`Eq` pair on `GroupKey`, the
//! equivalence relation used to *merge* rows is exactly the relation used to
//! *order* them — equality semantics and sort keys are consistent.

use crate::batch::InputBatch;
use crate::collation::{Collator, GroupKey};
use crate::error::Result;

use super::group::{ExecOutput, Group, KeyWarnings};
use super::keyed::key_rows;

/// Execute a sort-based group-by.
pub fn run(batch: &InputBatch, collator: &Collator) -> Result<ExecOutput> {
    let cols = batch.columns()?;
    let mut rows = key_rows(&cols, collator);

    // Stable sort: equal keys preserve input order, fixing the representative
    // (first member encountered) deterministically. The explicit row-index
    // tiebreaker states the contract even if an unstable sort were swapped in.
    rows.sort_by(|a, b| {
        a.key
            .cmp(&b.key)
            .then_with(|| a.row_index.cmp(&b.row_index))
    });

    let mut groups: Vec<Group> = Vec::new();
    let mut warnings = KeyWarnings::default();

    let mut pending: Option<(GroupKey, Group)> = None;

    for r in rows {
        if r.overflow {
            warnings.numeric_overflow.push(r.record_id.clone());
        }
        let opens_new = match pending.as_ref() {
            Some((key, _)) => key != &r.key,
            None => true,
        };
        if opens_new {
            if let Some((_, finished)) = pending.take() {
                groups.push(finished);
            }
            pending = Some((
                r.key.clone(),
                Group {
                    representative: r.raw.clone().unwrap_or_default(),
                    representative_record_id: r.record_id.clone(),
                    member_record_ids: vec![r.record_id],
                    count: 1,
                    is_null: r.raw.is_none(),
                },
            ));
        } else if let Some((_, g)) = pending.as_mut() {
            g.count += 1;
            g.member_record_ids.push(r.record_id);
        }
    }
    if let Some((_, finished)) = pending {
        groups.push(finished);
    }

    Ok(ExecOutput { groups, warnings })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::Row;
    use crate::collation::Rule;

    #[test]
    fn groups_accent_case_equivalents_and_keeps_first_representative() -> Result<()> {
        let rows = vec![
            Row::new("r1", "Café"),
            Row::new("r2", "cafe\u{0301}"),
            Row::new("r3", "CAFE"),
        ];
        let out = run(&InputBatch::from_rows(&rows)?, &Collator::new(Rule::v1()))?;
        assert_eq!(out.group_count(), 1);
        let g = &out.groups[0];
        assert_eq!(g.representative, "Café"); // first-in-order raw value
        assert_eq!(g.representative_record_id, "r1");
        assert_eq!(g.count, 3);
        assert_eq!(g.member_record_ids, vec!["r1", "r2", "r3"]);
        Ok(())
    }

    #[test]
    fn natural_numbers_order_groups() -> Result<()> {
        let rows = vec![
            Row::new("a", "file10"),
            Row::new("b", "file2"),
            Row::new("c", "file2"),
        ];
        let out = run(&InputBatch::from_rows(&rows)?, &Collator::new(Rule::v1()))?;
        assert_eq!(out.group_count(), 2);
        // Sorted order: file2 group first, then file10.
        assert_eq!(out.groups[0].representative, "file2");
        assert_eq!(out.groups[0].count, 2);
        assert_eq!(out.groups[1].representative, "file10");
        Ok(())
    }
}
