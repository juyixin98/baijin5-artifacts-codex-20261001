//! Query operators: sort-aggregation and hash-aggregation grouping/dedup executors.
//!
//! Both executors consume the SAME [`StringBatch`] and the SAME [`CollationRule`], and
//! produce the SAME logical [`GroupSet`], which is exactly what the comparison contract
//! asserts. They differ only in how equivalence classes are located:
//! * [`sort_groups`] — build a [`SortKey`] per row, sort, then merge equal adjacent keys;
//! * [`hash_groups`] — bucket by the deterministic [`key_hash`], then resolve collisions
//!   by full-key equality.

mod group;
mod hash_agg;
mod sort_agg;

pub use group::{Group, GroupSet};
pub use hash_agg::hash_groups;
pub use sort_agg::sort_groups;

use crate::batch::StringBatch;
use crate::collation::{canonical_bytes, sort_key, CollationRule, RepresentativePolicy, SortKey};

/// One equivalence class: the aggregation key, one representative ORIGINAL value,
/// the distinct original identities, and their input row positions.
#[derive(Debug)]
pub(crate) struct Assembled {
    key: SortKey,
    representative: String,
    identities: Vec<String>,
    rows: Vec<usize>,
}

fn assemble(
    rule: &CollationRule,
    batch: &StringBatch,
    mut member_rows: Vec<usize>,
    policy: RepresentativePolicy,
) -> Assembled {
    // Present members/identities in first-seen (input) order regardless of how the
    // executor discovered them.
    member_rows.sort_unstable();
    // Representative selection over ORIGINAL identities — never over folded keys.
    let representative = select_representative(batch, &member_rows, policy);
    let identities = member_rows
        .iter()
        .map(|&i| batch.values()[i].clone())
        .collect();
    Assembled {
        key: sort_key(rule, &representative),
        representative,
        identities,
        rows: member_rows,
    }
}

fn select_representative(
    batch: &StringBatch,
    rows: &[usize],
    policy: RepresentativePolicy,
) -> String {
    let pick = match policy {
        RepresentativePolicy::FirstWins => *rows.first().unwrap(),
        RepresentativePolicy::LastWins => *rows.last().unwrap(),
        RepresentativePolicy::ShortestWins => *rows
            .iter()
            .min_by_key(|&&i| (batch.values()[i].chars().count(), i))
            .unwrap(),
    };
    batch.values()[pick].clone()
}

fn to_group_set(
    rule: &CollationRule,
    batch: &StringBatch,
    assembled: Vec<Assembled>,
    order: GroupOrder,
) -> GroupSet {
    let mut groups = assembled
        .into_iter()
        .map(|a| Group {
            key_hex: hex_encode(&canonical_bytes(&a.key)),
            rule_version: rule.version.as_str().to_owned(),
            representative: a.representative,
            distinct_identities: unique_in_order(a.identities),
            row_count: a.rows.len(),
            rows: a.rows,
            hash: crate::collation::key_hash(&a.key),
        })
        .collect::<Vec<_>>();

    match order {
        GroupOrder::SortKey => groups.sort_by(|a, b| a.key_hex.cmp(&b.key_hex)),
        GroupOrder::FirstSeen => {}
    }
    GroupSet {
        column: batch.column().to_owned(),
        rule_version: rule.version.as_str().to_owned(),
        groups,
    }
}

fn unique_in_order(values: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    values
        .into_iter()
        .filter(|v| seen.insert(v.clone()))
        .collect()
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0x0f) as usize] as char);
    }
    s
}

/// Output ordering for groups (only affects presentation, not membership).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupOrder {
    /// Stable first-seen order (hash-agg native order).
    FirstSeen,
    /// Ordered by canonical key bytes (sort-agg native order).
    SortKey,
}
