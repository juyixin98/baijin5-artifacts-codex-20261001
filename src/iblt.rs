//! The IBLT encode/recover kernel.
//!
//! A table is `cells` buckets of `(count, key_sum, hash_sum)`:
//!
//! - `count` is a signed tally: `insert` adds 1, `remove` subtracts 1, so
//!   in a difference table a negative count marks a key of the *reverse*
//!   difference (present in B, missing in A).
//! - `key_sum` is the XOR of all keys mapped to the cell.
//! - `hash_sum` is the XOR of the key checksums mapped to the cell.
//!
//! Recovery (`decode`) peels **pure** cells: `count == ±1` *and*
//! `hash_sum == checksum(key_sum)`. The checksum check is mandatory —
//! a count of ±1 alone never justifies trusting `key_sum`. Peeling stops
//! when no pure cell remains; if any cell is still non-empty the decode
//! is incomplete and [`IbltError::DecodeIncomplete`] is returned instead
//! of a partial result.

use std::collections::VecDeque;

use crate::error::IbltError;
use crate::hash::{Hasher, DEFAULT_K, DEFAULT_SEED, MAX_K};

/// Table shape. Two tables may only be subtracted when all three fields
/// match; the binary format and the HTTP API both carry these.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Params {
    pub cells: u32,
    pub k: u32,
    pub seed: u64,
}

impl Params {
    /// Deterministic default for a set of `n` keys: ~1.5x headroom,
    /// at least 8 cells so `k = 3` always fits.
    pub fn for_set_size(n: usize) -> Self {
        let cells = ((n * 3).div_ceil(2)).max(8) as u32;
        Params { cells, k: DEFAULT_K, seed: DEFAULT_SEED }
    }
}

/// One IBLT bucket. All arithmetic is wrapping/XOR so insert and remove
/// are exact inverses.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Cell {
    pub count: i64,
    pub key_sum: u64,
    pub hash_sum: u64,
}

impl Cell {
    pub fn is_empty(&self) -> bool {
        self.count == 0 && self.key_sum == 0 && self.hash_sum == 0
    }

    /// A cell is pure only when the tally says exactly one key remains,
    /// the checksum confirms `key_sum` is that key, *and* the cell sits
    /// at one of that key's hash positions. Count alone is never
    /// sufficient evidence; the position check stops corrupt tables
    /// from triggering peels that would damage unrelated cells.
    fn is_pure(&self, hasher: &Hasher, index: usize) -> bool {
        (self.count == 1 || self.count == -1)
            && self.hash_sum == hasher.key_checksum(self.key_sum)
            && hasher.indices(self.key_sum).contains(&index)
    }
}

/// What a successful decode recovered. `only_a` holds keys of the
/// forward difference (count +1), `only_b` the reverse (count -1).
/// Both are sorted and deduplicated.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodeReport {
    pub only_a: Vec<u64>,
    pub only_b: Vec<u64>,
    /// Total keys peeled; useful intermediate state for logs and tests.
    pub peeled: usize,
}

/// An invertible Bloom lookup table.
#[derive(Debug, Clone)]
pub struct Iblt {
    params: Params,
    hasher: Hasher,
    cells: Vec<Cell>,
}

impl Iblt {
    /// Create an empty table, validating the shape.
    pub fn new(params: Params) -> Result<Self, IbltError> {
        validate_params(&params)?;
        Ok(Self {
            params,
            hasher: Hasher::new(params.seed, params.k, params.cells as usize),
            cells: vec![Cell::default(); params.cells as usize],
        })
    }

    /// Rebuild a table from raw cells (used by the binary format parser).
    pub fn from_cells(params: Params, cells: Vec<Cell>) -> Result<Self, IbltError> {
        validate_params(&params)?;
        if cells.len() != params.cells as usize {
            return Err(IbltError::InvalidInput(format!(
                "params declare {} cells but {} were supplied",
                params.cells,
                cells.len()
            )));
        }
        Ok(Self {
            params,
            hasher: Hasher::new(params.seed, params.k, params.cells as usize),
            cells,
        })
    }

    pub fn params(&self) -> Params {
        self.params
    }

    pub fn cells(&self) -> &[Cell] {
        &self.cells
    }

    pub fn nonzero_cells(&self) -> usize {
        self.cells.iter().filter(|c| !c.is_empty()).count()
    }

    /// Add `key` to the set (count +1 in each of its cells).
    pub fn insert(&mut self, key: u64) {
        self.apply(key, 1);
    }

    /// Remove `key` from the set (count -1). Exact inverse of `insert`.
    pub fn remove(&mut self, key: u64) {
        self.apply(key, -1);
    }

    fn apply(&mut self, key: u64, sign: i64) {
        let checksum = self.hasher.key_checksum(key);
        for idx in self.hasher.indices(key) {
            let cell = &mut self.cells[idx];
            cell.count += sign;
            cell.key_sum ^= key;
            cell.hash_sum ^= checksum;
        }
    }

    /// Cell-wise difference `self - other`. Counts subtract (so keys only
    /// in `other` end up with negative counts); XOR fields combine.
    pub fn subtract(&self, other: &Iblt) -> Result<Iblt, IbltError> {
        if self.params != other.params {
            return Err(IbltError::StateConflict(format!(
                "cannot subtract tables with different params: {:?} vs {:?}",
                self.params, other.params
            )));
        }
        let cells = self
            .cells
            .iter()
            .zip(other.cells.iter())
            .map(|(a, b)| Cell {
                count: a.count - b.count,
                key_sum: a.key_sum ^ b.key_sum,
                hash_sum: a.hash_sum ^ b.hash_sum,
            })
            .collect();
        Iblt::from_cells(self.params, cells)
    }

    /// Recover both sides of the difference.
    ///
    /// On success every cell drained to zero and the returned sets are
    /// complete. On stall this returns [`IbltError::DecodeIncomplete`]
    /// with the remaining non-empty cell count — never a partial set.
    pub fn decode(&self) -> Result<DecodeReport, IbltError> {
        let mut cells = self.cells.to_vec();
        let mut queue: VecDeque<usize> = (0..cells.len())
            .filter(|&i| cells[i].is_pure(&self.hasher, i))
            .collect();

        let mut only_a = std::collections::BTreeSet::new();
        let mut only_b = std::collections::BTreeSet::new();
        let mut peeled = 0usize;
        // Defensive bound: each peel strictly reduces the total |count|,
        // so this can only trip on corrupted state.
        let step_cap = (cells.len().max(1)) * 64 + 1024;

        while let Some(i) = queue.pop_front() {
            let cell = cells[i];
            if !cell.is_pure(&self.hasher, i) {
                continue; // stale queue entry, already drained below ±1
            }
            let key = cell.key_sum;
            let sign = cell.count.signum();
            if sign > 0 {
                only_a.insert(key);
            } else {
                only_b.insert(key);
            }
            let checksum = self.hasher.key_checksum(key);
            for idx in self.hasher.indices(key) {
                let c = &mut cells[idx];
                c.count -= sign;
                c.key_sum ^= key;
                c.hash_sum ^= checksum;
                if c.is_pure(&self.hasher, idx) {
                    queue.push_back(idx);
                }
            }
            peeled += 1;
            if peeled > step_cap {
                return Err(IbltError::ComputationFailed(format!(
                    "peel step cap {step_cap} exceeded on a {}-cell table",
                    cells.len()
                )));
            }
        }

        let remaining = cells.iter().filter(|c| !c.is_empty()).count();
        if remaining > 0 {
            return Err(IbltError::DecodeIncomplete {
                remaining_nonzero_cells: remaining,
                peeled,
            });
        }
        Ok(DecodeReport {
            only_a: only_a.into_iter().collect(),
            only_b: only_b.into_iter().collect(),
            peeled,
        })
    }
}

fn validate_params(p: &Params) -> Result<(), IbltError> {
    if p.cells == 0 {
        return Err(IbltError::InvalidInput("cells must be >= 1".into()));
    }
    if p.k == 0 || p.k > MAX_K {
        return Err(IbltError::InvalidInput(format!(
            "k must be in 1..={MAX_K}, got {}",
            p.k
        )));
    }
    if p.cells < p.k {
        return Err(IbltError::InvalidInput(format!(
            "cells ({}) must be >= k ({}) so index hashes can be distinct",
            p.cells, p.k
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params(cells: u32) -> Params {
        Params { cells, k: DEFAULT_K, seed: DEFAULT_SEED }
    }

    #[test]
    fn insert_then_remove_leaves_empty_table() {
        let mut t = Iblt::new(params(16)).unwrap();
        t.insert(123);
        t.remove(123);
        assert!(t.cells().iter().all(|c| c.is_empty()));
    }

    #[test]
    fn rejects_invalid_params() {
        assert!(Iblt::new(Params { cells: 0, k: 3, seed: 0 }).is_err());
        assert!(Iblt::new(Params { cells: 8, k: 0, seed: 0 }).is_err());
        assert!(Iblt::new(Params { cells: 2, k: 3, seed: 0 }).is_err());
        assert!(Iblt::new(Params { cells: 8, k: 9, seed: 0 }).is_err());
    }

    #[test]
    fn subtract_requires_matching_params() {
        let a = Iblt::new(params(16)).unwrap();
        let b = Iblt::new(params(32)).unwrap();
        let err = a.subtract(&b).unwrap_err();
        assert_eq!(err.category(), crate::error::ErrorCategory::StateConflict);
    }
}
