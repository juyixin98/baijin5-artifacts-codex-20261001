//! Resident multiplicity table with explicit byte accounting and checked
//! arithmetic. The map keys are full [`RowKey`]s: lookup hashes with the
//! precomputed canonical hash but equality compares every byte, which is the
//! "hash collision still requires real row comparison" guarantee.

use std::collections::HashMap;

use crate::batch::encode::RowKey;
use crate::error::Result;
use crate::resource::ResourceLimits;

#[derive(Debug, Default)]
pub(crate) struct CountTable {
    map: HashMap<RowKey, u64>,
    /// Sum of canonical key lengths currently resident.
    bytes: usize,
}

impl CountTable {
    pub(crate) fn add(&mut self, key: RowKey, delta: u64, limits: &ResourceLimits) -> Result<u64> {
        debug_assert!(delta > 0);
        match self.map.get_mut(&key) {
            Some(slot) => {
                let next = slot
                    .checked_add(delta)
                    .ok_or_else(|| limits.count_overflow_err())?;
                if next > limits.max_count {
                    return Err(limits.count_overflow_err());
                }
                *slot = next;
                Ok(next)
            }
            None => {
                if delta > limits.max_count {
                    return Err(limits.count_overflow_err());
                }
                self.bytes = self.bytes.saturating_add(key.bytes().len());
                self.map.insert(key, delta);
                Ok(delta)
            }
        }
    }

    pub(crate) fn resident_bytes(&self) -> usize {
        self.bytes
    }

    pub(crate) fn distinct(&self) -> usize {
        self.map.len()
    }

    pub(crate) fn drain(&mut self) -> impl Iterator<Item = (RowKey, u64)> + '_ {
        self.bytes = 0;
        self.map.drain()
    }

    pub(crate) fn iter(&self) -> impl Iterator<Item = (&RowKey, &u64)> {
        self.map.iter()
    }

    pub(crate) fn get(&self, key: &RowKey) -> Option<u64> {
        self.map.get(key).copied()
    }
}

/// Checked multiset combination. ALL semantics are arithmetic; DISTINCT
/// collapses presence. Returning 0 means "no output row".
pub(crate) fn combine(
    op: crate::operator::SetOp,
    qualifier: crate::operator::Qualifier,
    left: Option<u64>,
    right: Option<u64>,
    limits: &ResourceLimits,
) -> Result<u64> {
    use crate::operator::{Qualifier, SetOp};
    match (op, qualifier) {
        (SetOp::Union, Qualifier::Distinct) => Ok(u64::from(left.or(right).is_some())),
        (SetOp::Union, Qualifier::All) => {
            let l = left.unwrap_or(0);
            let r = right.unwrap_or(0);
            let n = l
                .checked_add(r)
                .ok_or_else(|| limits.count_overflow_err())?;
            if n > limits.max_count {
                return Err(limits.count_overflow_err());
            }
            Ok(n)
        }
        (SetOp::Intersect, Qualifier::Distinct) => Ok(u64::from(left.is_some() && right.is_some())),
        (SetOp::Intersect, Qualifier::All) => Ok(left.unwrap_or(0).min(right.unwrap_or(0))),
        (SetOp::Except, Qualifier::Distinct) => Ok(u64::from(left.is_some() && right.is_none())),
        (SetOp::Except, Qualifier::All) => Ok(left.unwrap_or(0).saturating_sub(right.unwrap_or(0))),
    }
}
