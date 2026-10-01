//! Sort-based grouping executor.

use crate::batch::StringBatch;
use crate::collation::{sort_key, CollationRule, RepresentativePolicy, SortKey};

use super::{assemble, to_group_set, Assembled, GroupOrder, GroupSet};

/// Group by computing sort keys, sorting by them, and merging adjacent equal keys.
///
/// Equality and adjacency both derive from the total order on [`SortKey`], so equal
/// keys are guaranteed to become adjacent — this is the definition of sort-agg.
pub fn sort_groups(
    rule: &CollationRule,
    batch: &StringBatch,
    policy: RepresentativePolicy,
) -> GroupSet {
    // (sort key, original row index)
    let mut keyed: Vec<(SortKey, usize)> = batch
        .iter()
        .enumerate()
        .map(|(i, v)| (sort_key(rule, v), i))
        .collect();

    keyed.sort_by(|a, b| a.0.compare(&b.0).then(a.1.cmp(&b.1)));

    let mut assembled: Vec<Assembled> = Vec::new();
    let mut current_rows: Vec<usize> = Vec::new();
    let mut current_key: Option<SortKey> = None;

    for (key, row) in keyed {
        match &current_key {
            Some(prev) if prev.equivalent(&key) => current_rows.push(row),
            Some(_) => {
                assembled.push(assemble(
                    rule,
                    batch,
                    std::mem::take(&mut current_rows),
                    policy,
                ));
                current_rows.push(row);
                current_key = Some(key);
            }
            None => {
                current_rows.push(row);
                current_key = Some(key);
            }
        }
    }
    if !current_rows.is_empty() {
        assembled.push(assemble(rule, batch, current_rows, policy));
    }

    // Groups were discovered in sort-key order; `assemble` normalizes inner rows.
    to_group_set(rule, batch, assembled, GroupOrder::SortKey)
}

#[cfg(test)]
mod tests {
    use crate::collation::{rule_for, RuleVersion};

    use super::*;

    #[test]
    fn merges_equivalent_adjacent_keys() {
        let rule = rule_for(RuleVersion::V2026R1).unwrap();
        let batch = StringBatch::try_new(
            "s",
            vec![
                "Café".to_string(),
                "cafe".to_string(),
                "CAFE".to_string(),
                "b".to_string(),
            ],
        )
        .unwrap();
        let gs = sort_groups(rule, &batch, RepresentativePolicy::FirstWins);
        assert_eq!(gs.group_count(), 2);
        assert_eq!(gs.total_rows(), 4);
    }
}
