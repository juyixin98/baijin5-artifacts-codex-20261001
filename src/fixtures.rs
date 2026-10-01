//! Local synthetic fixtures loaded into the catalog. All data is generated in
//! code — no external accounts or real business data.
use std::sync::Arc;

use crate::batch::{ColumnSchema, RelationSchema, TypedBatch};
use crate::domain::{Datum, LogicalType};
use crate::resource::Catalog;

fn schema(pairs: &[(&str, LogicalType)]) -> Arc<RelationSchema> {
    Arc::new(RelationSchema::new(
        pairs
            .iter()
            .map(|(n, t)| ColumnSchema::new(*n, *t))
            .collect(),
    ))
}

fn int_rows(rows: &[Vec<i64>]) -> Vec<Vec<Datum>> {
    rows.iter()
        .map(|r| r.iter().map(|v| Datum::Int(*v)).collect())
        .collect()
}

/// Symmetric directed edge set of the complete graph K_n: every unordered
/// pair appears as both (i,j) and (j,i).
fn complete_directed_edges(n: i64) -> Vec<Vec<i64>> {
    let mut edges = Vec::new();
    for i in 0..n {
        for j in 0..n {
            if i != j {
                edges.push(vec![i, j]);
            }
        }
    }
    edges
}

/// Triangle fixtures: three relations E(a,b), F(b,c), G(c,a) over the same
/// K4 edge set. Expected output of their natural join: each of the 4 unordered
/// triangles appears in 6 orientations = 24 tuples, each multiplicity 1.
pub fn load_triangle(catalog: &mut Catalog) {
    let s_ab = schema(&[("a", LogicalType::Int64), ("b", LogicalType::Int64)]);
    let s_bc = schema(&[("b", LogicalType::Int64), ("c", LogicalType::Int64)]);
    let s_ca = schema(&[("c", LogicalType::Int64), ("a", LogicalType::Int64)]);
    let edges = int_rows(&complete_directed_edges(4));
    catalog.insert(
        "tri_e",
        s_ab.clone(),
        TypedBatch::from_rows(s_ab, edges.clone()).expect("fixture"),
    );
    catalog.insert(
        "tri_f",
        s_bc.clone(),
        TypedBatch::from_rows(s_bc, edges.clone()).expect("fixture"),
    );
    catalog.insert(
        "tri_g",
        s_ca.clone(),
        TypedBatch::from_rows(s_ca, edges).expect("fixture"),
    );
}

/// Highly skewed join plus a tiny selective relation.
///
/// * `skew_ab(a,b)`: a=0 paired with b = 0..999 (1000 rows), plus a=1,b=0;
/// * `skew_ac(a,c)`: a=0 paired with c = 0..999 (1000 rows);
/// * `pick_b(b)`: a single highly selective row b=0.
///
/// A naive left-deep `ab ⋈ ac` builds a 1,000,000-row intermediate before
/// `pick_b` filters to 1000 output rows. Leapfrog intersects `pick_b` at the
/// `b` level first and never builds that product. Final result:
/// (a=0,b=0,c=0..999) = 1000 tuples.
pub fn load_skew(catalog: &mut Catalog) {
    let s_ab = schema(&[("a", LogicalType::Int64), ("b", LogicalType::Int64)]);
    let s_ac = schema(&[("a", LogicalType::Int64), ("c", LogicalType::Int64)]);
    let s_b = schema(&[("b", LogicalType::Int64)]);

    let mut ab: Vec<Vec<i64>> = (0..1000).map(|b| vec![0, b]).collect();
    ab.push(vec![1, 0]);
    let ac: Vec<Vec<i64>> = (0..1000).map(|c| vec![0, c]).collect();
    let pick = vec![vec![0i64]];

    catalog.insert(
        "skew_ab",
        s_ab.clone(),
        TypedBatch::from_rows(s_ab, int_rows(&ab)).expect("fixture"),
    );
    catalog.insert(
        "skew_ac",
        s_ac.clone(),
        TypedBatch::from_rows(s_ac, int_rows(&ac)).expect("fixture"),
    );
    catalog.insert(
        "pick_b",
        s_b.clone(),
        TypedBatch::from_rows(s_b, int_rows(&pick)).expect("fixture"),
    );
}

/// Sparse-intersection fixture: two large domains sharing only a few keys.
///
/// `sp_x(k,v)` holds keys 0..2000 and `sp_y(k,w)` holds keys 2001..4001,
/// except `sp_y` additionally contains keys 5, 7, 9. The two main ranges are
/// disjoint and far apart, so intersecting them takes a handful of binary
/// leaps rather than a 2000-step merge. Expected output: 3 tuples
/// (k = 5, 7, 9).
pub fn load_sparse(catalog: &mut Catalog) {
    let s_x = schema(&[("k", LogicalType::Int64), ("v", LogicalType::Int64)]);
    let s_y = schema(&[("k", LogicalType::Int64), ("w", LogicalType::Int64)]);

    let x: Vec<Vec<i64>> = (0..2000i64).map(|k| vec![k, k + 1]).collect();
    let mut y: Vec<Vec<i64>> = (2001..=4001).map(|k| vec![k, k * 10]).collect();
    y.extend_from_slice(&[vec![5, 50], vec![7, 70], vec![9, 90]]);

    catalog.insert(
        "sp_x",
        s_x.clone(),
        TypedBatch::from_rows(s_x, int_rows(&x)).expect("fixture"),
    );
    catalog.insert(
        "sp_y",
        s_y.clone(),
        TypedBatch::from_rows(s_y, int_rows(&y)).expect("fixture"),
    );
}

/// Duplicate/multiplicity fixture for bag semantics.
///
/// `dup_l(x,y)` repeats (1,10) three times; `dup_r(x,z)` repeats (1,100)
/// twice. The joined tuple (x=1) carries multiplicity 3*2=6.
pub fn load_duplicates(catalog: &mut Catalog) {
    let s_l = schema(&[("x", LogicalType::Int64), ("y", LogicalType::Int64)]);
    let s_r = schema(&[("x", LogicalType::Int64), ("z", LogicalType::Int64)]);
    let l = vec![vec![1i64, 10], vec![1, 10], vec![1, 10], vec![2, 20]];
    let r = vec![vec![1i64, 100], vec![1, 100]];
    catalog.insert(
        "dup_l",
        s_l.clone(),
        TypedBatch::from_rows(s_l, int_rows(&l)).expect("fixture"),
    );
    catalog.insert(
        "dup_r",
        s_r.clone(),
        TypedBatch::from_rows(s_r, int_rows(&r)).expect("fixture"),
    );
}

/// Populate every built-in fixture.
pub fn build_default_catalog() -> Catalog {
    let mut catalog = Catalog::new();
    load_triangle(&mut catalog);
    load_skew(&mut catalog);
    load_sparse(&mut catalog);
    load_duplicates(&mut catalog);
    catalog
}
