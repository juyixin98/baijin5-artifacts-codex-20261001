//! Kernel recovery tests: known small diffs, duplicates, forced
//! collisions, overload, and checksum-verified peeling.
//!
//! Expected sets are computed here with plain `BTreeSet` difference —
//! direct set arithmetic, independent of the IBLT kernel under test.

use std::collections::BTreeSet;

use iblt_service::error::{ErrorCategory, IbltError};
use iblt_service::hash::{Hasher, DEFAULT_K, DEFAULT_SEED};
use iblt_service::iblt::{Cell, Iblt, Params};

fn params(cells: u32) -> Params {
    Params { cells, k: DEFAULT_K, seed: DEFAULT_SEED }
}

fn table_from(keys: &BTreeSet<u64>, cells: u32) -> Iblt {
    let mut t = Iblt::new(params(cells)).unwrap();
    for &k in keys {
        t.insert(k);
    }
    t
}

fn range(lo: u64, hi: u64) -> BTreeSet<u64> {
    (lo..=hi).collect()
}

#[test]
fn recovers_known_small_diff_both_sides() {
    let a = range(1, 12);
    let b = range(7, 18);
    let diff = table_from(&a, 64).subtract(&table_from(&b, 64)).unwrap();
    let report = diff.decode().expect("12-key diff in 64 cells must decode");

    let expected_a: Vec<u64> = a.difference(&b).copied().collect();
    let expected_b: Vec<u64> = b.difference(&a).copied().collect();
    assert_eq!(report.only_a, expected_a, "forward difference mismatch");
    assert_eq!(report.only_b, expected_b, "reverse difference mismatch");
    assert_eq!(report.peeled, expected_a.len() + expected_b.len());
}

#[test]
fn empty_difference_decodes_to_empty_sets() {
    let keys = range(1, 20);
    let diff = table_from(&keys, 32).subtract(&table_from(&keys, 32)).unwrap();
    assert!(
        diff.cells().iter().all(|c| c.is_empty()),
        "identical sets must cancel to an all-zero table"
    );
    let report = diff.decode().unwrap();
    assert!(report.only_a.is_empty() && report.only_b.is_empty());
    assert_eq!(report.peeled, 0);
}

#[test]
fn negative_counts_mark_the_reverse_difference() {
    let a: BTreeSet<u64> = [1, 2, 3].into_iter().collect();
    let b: BTreeSet<u64> = [2, 3, 4].into_iter().collect();
    let diff = table_from(&a, 16).subtract(&table_from(&b, 16)).unwrap();
    assert!(
        diff.cells().iter().any(|c| c.count < 0),
        "keys only in B must produce negative cell counts"
    );
    let report = diff.decode().unwrap();
    assert_eq!(report.only_a, vec![1]);
    assert_eq!(report.only_b, vec![4]);
}

#[test]
fn duplicate_insert_is_not_silently_decodable() {
    // A set cannot contain the same key twice: inserting 5 twice leaves
    // count = 2 in its cells, which is never pure, so decode must fail
    // rather than guess.
    let mut t = Iblt::new(params(16)).unwrap();
    t.insert(5);
    t.insert(5);
    let empty = Iblt::new(params(16)).unwrap();
    let err = t.subtract(&empty).unwrap().decode().unwrap_err();
    assert!(
        matches!(err, IbltError::DecodeIncomplete { .. }),
        "duplicate insert must stall decode, got {err:?}"
    );
    assert_eq!(err.category(), ErrorCategory::ResourceExhausted);
}

#[test]
fn forced_index_collision_stalls_decode() {
    // cells = k = 1 forces every key into the single shared cell: the
    // XOR sums no longer match any single key's checksum.
    let p = Params { cells: 1, k: 1, seed: DEFAULT_SEED };
    let mut t = Iblt::new(p).unwrap();
    t.insert(5);
    t.insert(9);
    let err = t.decode().unwrap_err();
    match err {
        IbltError::DecodeIncomplete { remaining_nonzero_cells, .. } => {
            assert_eq!(remaining_nonzero_cells, 1);
        }
        other => panic!("expected DecodeIncomplete, got {other:?}"),
    }
}

#[test]
fn overloaded_table_reports_incomplete_never_partial() {
    // 40 differing keys in a 16-cell table cannot decode; the error must
    // carry diagnostic state and no result sets.
    let a = range(1, 40);
    let b: BTreeSet<u64> = BTreeSet::new();
    let diff = table_from(&a, 16).subtract(&table_from(&b, 16)).unwrap();
    let err = diff.decode().unwrap_err();
    match err {
        IbltError::DecodeIncomplete { remaining_nonzero_cells, peeled } => {
            assert!(remaining_nonzero_cells > 0);
            assert!(peeled + remaining_nonzero_cells > 0, "diagnostic state must be populated");
        }
        other => panic!("expected DecodeIncomplete, got {other:?}"),
    }
}

#[test]
fn pure_cell_requires_checksum_match_not_just_count() {
    // Real table holding key 42, plus a forged cell that has count = 1
    // and key_sum = 99 but a checksum that does NOT match 99. Decode
    // must peel 42, refuse to trust 99, and fail incomplete — a count of
    // ±1 alone is not evidence of purity.
    let p = params(8);
    let hasher = Hasher::new(p.seed, p.k, p.cells as usize);
    let mut real = Iblt::new(p).unwrap();
    real.insert(42);
    let mut cells = real.cells().to_vec();
    let slot = cells.iter().position(|c| c.is_empty()).expect("an empty cell exists");
    cells[slot] = Cell { count: 1, key_sum: 99, hash_sum: hasher.key_checksum(99) ^ 0xDEAD };
    let t = Iblt::from_cells(p, cells).unwrap();

    let err = t.decode().unwrap_err();
    match err {
        IbltError::DecodeIncomplete { remaining_nonzero_cells, peeled } => {
            assert_eq!(peeled, 1, "only the checksum-verified key may be peeled");
            assert_eq!(remaining_nonzero_cells, 1, "the forged cell must remain");
        }
        other => panic!("expected DecodeIncomplete, got {other:?}"),
    }
}

#[test]
fn checksum_match_at_wrong_position_is_not_peeled() {
    // A forged cell whose checksum genuinely matches key_sum 99 but
    // which sits at a position key 99 never hashes to. Even a valid
    // checksum is not enough: the cell position must be one of the
    // key's hash positions, so this cell is never peeled.
    let p = params(8);
    let hasher = Hasher::new(p.seed, p.k, p.cells as usize);
    let mut real = Iblt::new(p).unwrap();
    real.insert(42);
    let mut cells = real.cells().to_vec();
    let slot = (0..8)
        .find(|&i| cells[i].is_empty() && !hasher.indices(99).contains(&i))
        .expect("an empty cell outside indices(99) exists");
    cells[slot] = Cell { count: 1, key_sum: 99, hash_sum: hasher.key_checksum(99) };
    let t = Iblt::from_cells(p, cells).unwrap();

    let err = t.decode().unwrap_err();
    match err {
        IbltError::DecodeIncomplete { remaining_nonzero_cells, peeled } => {
            assert_eq!(peeled, 1, "only key 42 may be peeled");
            assert_eq!(remaining_nonzero_cells, 1, "the misplaced cell must remain");
        }
        other => panic!("expected DecodeIncomplete, got {other:?}"),
    }
}

#[test]
fn tampered_checksum_is_detected_as_decode_failure() {
    // Take a real one-key table and corrupt one hash_sum byte: the key
    // can no longer be verified, so decode fails instead of emitting it.
    let mut t = Iblt::new(params(16)).unwrap();
    t.insert(777);
    let mut cells = t.cells().to_vec();
    let victim = cells.iter().position(|c| c.count == 1).expect("a pure cell exists");
    cells[victim].hash_sum ^= 0xFF;
    let tampered = Iblt::from_cells(params(16), cells).unwrap();
    assert!(matches!(tampered.decode(), Err(IbltError::DecodeIncomplete { .. })));
}

#[test]
fn subtract_with_mismatched_params_is_state_conflict() {
    let a = Iblt::new(params(16)).unwrap();
    let b = Iblt::new(Params { cells: 16, k: DEFAULT_K, seed: DEFAULT_SEED + 1 }).unwrap();
    let err = a.subtract(&b).unwrap_err();
    assert_eq!(err.category(), ErrorCategory::StateConflict);
    assert_eq!(err.code(), "state_conflict");
}
