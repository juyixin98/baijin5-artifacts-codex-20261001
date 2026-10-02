//! Independent multiset oracle.
//!
//! Test oracle implemented in a deliberately different style from the engine:
//! it uses a [`BTreeMap`] of canonical keys (the engine uses a partitioned
//! `HashMap` plus external segments), builds expected multiplicities with
//! plain arithmetic, and performs no hashing or spilling. Sharing only the
//! *encoding* (the definition of row equality) with the engine keeps the
//! reference independent of the measured partitioning/aggregation logic while
//! still agreeing on NULL/type/boundary semantics.
//!
//! Crucially, the oracle returns **per-row multiplicities**, so tests can
//! compare against the engine row-by-row (as a multiset), not just as a set.

use std::collections::BTreeMap;

use crate::encoding::encode_row;
use crate::executor::{Quantifier, SetOperator};
use crate::value::Value;

/// Multiset of rows keyed by canonical encoding: key -> multiplicity.
#[derive(Default)]
pub struct Multiset {
    counts: BTreeMap<Vec<u8>, u64>,
}

impl Multiset {
    pub fn from_rows(rows: &[Vec<Value>]) -> Self {
        let mut m = Multiset::default();
        for row in rows {
            *m.counts.entry(encode_row(row)).or_insert(0) += 1;
        }
        m
    }

    pub fn distinct_keys(&self) -> usize {
        self.counts.len()
    }

    pub fn total_rows(&self) -> u64 {
        self.counts.values().sum()
    }

    pub fn multiplicity_of(&self, row: &[Value]) -> u64 {
        self.counts.get(&encode_row(row)).copied().unwrap_or(0)
    }

    /// Reference semantics for the six operations. Returns key -> multiplicity.
    pub fn set_op(
        op: SetOperator,
        quant: Quantifier,
        left: &Multiset,
        right: &Multiset,
    ) -> Multiset {
        let mut out = BTreeMap::new();
        // Every key that appears on either side.
        let mut keys: std::collections::BTreeSet<&Vec<u8>> = left.counts.keys().collect();
        keys.extend(right.counts.keys());
        for k in keys {
            let l = left.counts.get(k).copied().unwrap_or(0);
            let r = right.counts.get(k).copied().unwrap_or(0);
            let n = match op {
                SetOperator::Union => match quant {
                    // Reference uses saturating arithmetic only because it is
                    // building an expected answer for inputs that are known to
                    // be in range; overflow rejection is asserted separately.
                    Quantifier::All => l + r,
                    Quantifier::Distinct => u64::from(l + r > 0),
                },
                SetOperator::Intersect => match quant {
                    Quantifier::All => l.min(r),
                    Quantifier::Distinct => u64::from(l > 0 && r > 0),
                },
                SetOperator::Except => match quant {
                    Quantifier::All => l.saturating_sub(r),
                    Quantifier::Distinct => u64::from(l > 0 && r == 0),
                },
            };
            if n > 0 {
                out.insert(k.clone(), n);
            }
        }
        Multiset { counts: out }
    }

    /// Compare two multisets exactly (same keys, same multiplicities).
    pub fn equals(&self, other: &Multiset) -> bool {
        self.counts == other.counts
    }

    /// Diff description for failure messages: which keys differ and how.
    pub fn diff_summary(left: &Multiset, right: &Multiset, max_show: usize) -> String {
        let mut keys: std::collections::BTreeSet<&Vec<u8>> = left.counts.keys().collect();
        keys.extend(right.counts.keys());
        let mut shown = 0;
        let mut parts = Vec::new();
        for k in &keys {
            let a = left.counts.get(*k).copied().unwrap_or(0);
            let b = right.counts.get(*k).copied().unwrap_or(0);
            if a != b {
                parts.push(format!("key{}: expected {a}, got {b}", hex_short(k)));
                shown += 1;
                if shown >= max_show {
                    break;
                }
            }
        }
        if parts.is_empty() {
            "no differences".to_string()
        } else {
            parts.join("; ")
        }
    }
}

fn hex_short(k: &[u8]) -> String {
    let preview = k
        .iter()
        .take(8)
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    format!("({}B:{preview}..)", k.len())
}
