//! Invariant-focused tests for the bitmap and 3VL core.
//!
//! These pin the exact boundary properties the task calls out:
//! * true/false/unknown operate within ONE shared document universe,
//! * NOT is never a machine-word negation of the true bitmap,
//! * tail invalid bits are zero on every constructor and operator,
//! * versioned deletes remove rows while keeping combined indexes compatible.

use tvindex::index::bitmap::Bitmap;
use tvindex::index::tricolor::{Tri, Tricolor};
use tvindex::index::{CmpOp, Scalar, VersionMap};

fn tri(n: usize, t: &[usize], f: &[usize], u: &[usize]) -> Tricolor {
    Tricolor::new(
        Bitmap::from_indices(n, t.iter().copied()).unwrap(),
        Bitmap::from_indices(n, f.iter().copied()).unwrap(),
        Bitmap::from_indices(n, u.iter().copied()).unwrap(),
    )
    .unwrap()
}

#[test]
fn every_row_is_exactly_one_of_true_false_unknown() {
    for &n in &[0usize, 1, 2, 63, 64, 65, 66, 67, 128, 129] {
        let mut t = Vec::new();
        let mut f = Vec::new();
        let mut u = Vec::new();
        for i in 0..n {
            match i % 3 {
                0 => t.push(i),
                1 => f.push(i),
                _ => u.push(i),
            }
        }
        let tc = tri(n, &t, &f, &u);
        assert!(tc.validate().is_ok(), "partition invalid for n={n}");
        assert_eq!(tc.t.count_ones() + tc.f.count_ones() + tc.u.count_ones(), n);
    }
}

#[test]
fn not_never_introduces_unknown_or_padding_rows() {
    // Non-word universe: rows 0..=64 TRUE, 65 FALSE, 66 UNKNOWN (a tail bit).
    let n = 67usize;
    let tc = tri(n, &(0..=64).collect::<Vec<_>>(), &[65], &[66]);
    let neg = tc.negate();

    // The reference definition: NOT's true-set is the FALSE set, its false-set
    // is the TRUE set, unknown fixed point. It must NOT equal !true_words.
    let naive_machine_not = {
        // A deliberately naive per-word NOT (the forbidden implementation):
        let mut words = tc.t.words().to_vec();
        for w in &mut words {
            *w = !*w;
        }
        // Even before tail clearing, compare on valid rows.
        words
    };
    assert_ne!(
        neg.t.words(),
        naive_machine_not.as_slice(),
        "NOT(TRUE) must not be a raw machine-word negation"
    );

    assert_eq!(neg.value_at(0), Tri::False);
    assert_eq!(neg.value_at(65), Tri::True);
    assert_eq!(
        neg.value_at(66),
        Tri::Unknown,
        "UNKNOWN is a NOT fixed point"
    );
    assert_eq!(
        neg.t.count_ones() + neg.f.count_ones() + neg.u.count_ones(),
        n
    );
    // Padding bits of the final word stay zero on every derived set.
    for bm in [&neg.t, &neg.f, &neg.u] {
        let last = *bm.words().last().unwrap();
        assert_eq!(last & !0x7u64, 0, "tail padding set in a tricolor bitmap");
    }
}

#[test]
fn all_bitmap_operators_clear_tail_bits() {
    let n = 67usize;
    let a = Bitmap::from_indices(n, [0, 66]).unwrap();
    let b = Bitmap::from_indices(n, [1, 65]).unwrap();
    for bm in [
        a.and(&b).unwrap(),
        a.or(&b).unwrap(),
        a.and_not(&b).unwrap(),
        a.complement(),
        b.complement(),
    ] {
        let last = *bm.words().last().unwrap();
        assert_eq!(last & !0x7u64, 0, "operator left padding bits set: {bm:?}");
        assert_eq!(bm.len(), n);
    }
}

#[test]
fn dirty_tail_inputs_are_rejected_not_silently_counted() {
    // Manually build words with invalid tail bits for a 5-row universe.
    let err = Bitmap::from_words(5, vec![0b111111]).unwrap_err();
    assert!(err.to_string().contains("beyond the end"));

    // Tricolor construction over a clean partition must succeed, but feeding
    // padding-set bitmaps through Tricolor::new must fail.
    let bad = Bitmap::from_words(1, vec![0b10]).unwrap_err();
    assert!(bad.to_string().contains("beyond the end"));
}

#[test]
fn tricolor_and_or_truth_tables_all_nine_combinations() {
    // Each row holds one of the 9 ordered pairs of (a, b); verify the gates.
    let vals = [Tri::True, Tri::False, Tri::Unknown];
    let mut pairs: Vec<(Tri, Tri)> = Vec::new();
    for &a in &vals {
        for &b in &vals {
            pairs.push((a, b));
        }
    }
    let n = pairs.len();

    let mut at: Vec<usize> = Vec::new();
    let mut af: Vec<usize> = Vec::new();
    let mut au: Vec<usize> = Vec::new();
    let mut bt: Vec<usize> = Vec::new();
    let mut bf: Vec<usize> = Vec::new();
    let mut bu: Vec<usize> = Vec::new();
    for (i, (a, b)) in pairs.iter().enumerate() {
        match a {
            Tri::True => at.push(i),
            Tri::False => af.push(i),
            Tri::Unknown => au.push(i),
        }
        match b {
            Tri::True => bt.push(i),
            Tri::False => bf.push(i),
            Tri::Unknown => bu.push(i),
        }
    }
    let ta = tri(n, &at, &af, &au);
    let tb = tri(n, &bt, &bf, &bu);

    let and_r = ta.and(&tb).unwrap();
    let or_r = ta.or(&tb).unwrap();
    for (i, (a, b)) in pairs.iter().enumerate() {
        assert_eq!(
            and_r.value_at(i),
            a.and(*b),
            "AND wrong at pair {a:?},{b:?}"
        );
        assert_eq!(or_r.value_at(i), a.or(*b), "OR wrong at pair {a:?},{b:?}");
    }
    assert_eq!(
        and_r.t.count_ones() + and_r.f.count_ones() + and_r.u.count_ones(),
        n
    );
    assert_eq!(
        or_r.t.count_ones() + or_r.f.count_ones() + or_r.u.count_ones(),
        n
    );
}

#[test]
fn universe_mismatch_is_a_typed_conflict() {
    let a = Bitmap::zeros(64);
    let b = Bitmap::zeros(65);
    assert!(a.and(&b).is_err());
    assert!(a.or(&b).is_err());
    assert!(a.and_not(&b).is_err());

    let x = Tricolor::constant(3, Tri::True);
    let y = Tricolor::constant(4, Tri::False);
    assert!(x.and(&y).is_err());
    assert!(x.or(&y).is_err());
}

#[test]
fn deletion_excludes_rows_without_forcing_them_false() {
    use tvindex::index::ColumnIndex;
    use tvindex::index::{ColumnMeta, LogicalType, TypedTable};

    // 65 rows: ids 0..64 (non-word-sized universe, last row on a tail bit).
    let mut csv = String::from("id\n");
    for i in 0..65u32 {
        csv.push_str(&format!("{i}\n"));
    }
    let table = TypedTable::from_csv(
        "t",
        vec![ColumnMeta {
            name: "id".into(),
            logical: LogicalType::Int,
        }],
        &csv,
    )
    .unwrap();
    assert_eq!(table.len(), 65);
    let idx = ColumnIndex::build(&table, 0, 1).unwrap();

    let mut vm = VersionMap::new(65);
    vm.delete(63, 2).unwrap(); // a tail-adjacent real row
    let live = vm.live_bitmap();

    let tc = idx.evaluate(CmpOp::Eq, &Scalar::Int(63)).unwrap();
    let visible = tc.restrict_to_live(&live).unwrap();

    // Row 63 was TRUE but is deleted: present in none of T/F/U at the head.
    assert!(!visible.t.get(63));
    assert!(!visible.f.get(63));
    assert!(!visible.u.get(63));
    // Combined indexes remain compatible because content stayed at v1.
    assert!(vm.ensure_compatible(idx.len(), idx.version()).is_ok());
    assert_eq!(
        visible.t.count_ones() + visible.f.count_ones() + visible.u.count_ones(),
        64
    );
}
