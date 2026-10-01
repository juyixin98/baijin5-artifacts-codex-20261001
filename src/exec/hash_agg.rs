//! Hash-based grouping executor.

use std::collections::HashMap;

use crate::batch::StringBatch;
use crate::collation::{key_hash, sort_key, CollationRule, RepresentativePolicy, SortKey};

use super::{assemble, to_group_set, Assembled, GroupOrder, GroupSet};

/// Group by bucketing on [`key_hash`](crate::collation::key_hash), resolving any bucket
/// collision with FULL sort-key equality. Equal values always produce the same key
/// bytes, hence the same hash and land in the same bucket — the compatibility
/// requirement for hash grouping.
pub fn hash_groups(
    rule: &CollationRule,
    batch: &StringBatch,
    policy: RepresentativePolicy,
) -> GroupSet {
    // hash bucket -> indices into `classes` (collision chain; normally length 1).
    let mut buckets: HashMap<u64, Vec<usize>> = HashMap::new();
    let mut classes: Vec<(SortKey, Vec<usize>)> = Vec::new();

    for (row, value) in batch.iter().enumerate() {
        let key = sort_key(rule, value);
        let hash = key_hash(&key);

        let chain = buckets.entry(hash).or_default();
        let mut found: Option<usize> = None;
        for &idx in chain.iter() {
            if classes[idx].0.equivalent(&key) {
                found = Some(idx);
                break;
            }
        }
        match found {
            Some(idx) => classes[idx].1.push(row),
            None => {
                chain.push(classes.len());
                classes.push((key, vec![row]));
            }
        }
    }

    let assembled: Vec<Assembled> = classes
        .into_iter()
        .map(|(_key, rows)| assemble(rule, batch, rows, policy))
        .collect();

    // Native hash-agg presentation: first-seen class order.
    to_group_set(rule, batch, assembled, GroupOrder::FirstSeen)
}

#[cfg(test)]
mod tests {
    use crate::collation::{rule_for, RuleVersion};

    use super::*;

    #[test]
    fn equivalent_values_share_a_bucket() {
        let rule = rule_for(RuleVersion::V2026R1).unwrap();
        let batch = StringBatch::try_new(
            "s",
            vec!["É2".to_string(), "e2".to_string(), "E02".to_string()],
        )
        .unwrap();
        let gs = hash_groups(
            rule,
            &batch,
            crate::collation::RepresentativePolicy::FirstWins,
        );
        assert_eq!(gs.group_count(), 1);
        assert_eq!(gs.total_rows(), 3);
    }
}
