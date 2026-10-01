//! Deterministically regenerate every bundled synthetic fixture under
//! `fixtures/`. No randomness, no external data: rerunning yields identical
//! files, so experiments are reproducible.
//!
//! Usage: `cargo run --example generate_fixtures`
//!
//! Datasets:
//! - triangle_{r,s,t}: oriented edges of the complete graph K5;
//! - skew_{r,s,t}: a hub whose naive intermediate prefixes blow up while the
//!   third relation intersects only five pairs.

use std::fs;
use std::path::PathBuf;

use leapfrog_triejoin::validate::{ColumnSpec, RelationSpec};
use leapfrog_triejoin::ServiceError;

fn int_relation(name: &str, cols: [&str; 2], rows: Vec<[i64; 2]>) -> RelationSpec {
    RelationSpec {
        name: name.to_string(),
        columns: cols
            .iter()
            .map(|c| ColumnSpec {
                name: (*c).to_string(),
                typ: "int".to_string(),
            })
            .collect(),
        rows: rows
            .into_iter()
            .map(|pair| {
                vec![
                    serde_json::Value::from(pair[0]),
                    serde_json::Value::from(pair[1]),
                ]
            })
            .collect(),
    }
}

/// Oriented edges i < j of the complete graph on `n` vertices.
fn complete_graph_edges(n: i64) -> Vec<[i64; 2]> {
    let mut edges = Vec::new();
    for i in 0..n {
        for j in (i + 1)..n {
            edges.push([i, j]);
        }
    }
    edges
}

/// R(a,b): hub value b=1 repeated for 200 distinct a, plus 100 sparse rows.
fn skew_r() -> RelationSpec {
    let mut rows: Vec<[i64; 2]> = (1..=200).map(|a| [a, 1]).collect();
    for b in 2..=101 {
        rows.push([b + 1000, b]);
    }
    int_relation("skew_r", ["a", "b"], rows)
}

/// S(b,c): hub value b=1 repeated for 200 distinct c, plus 100 sparse rows.
fn skew_s() -> RelationSpec {
    let mut rows: Vec<[i64; 2]> = (1..=200).map(|c| [1, c]).collect();
    for b in 2..=101 {
        rows.push([b, b + 9000]);
    }
    int_relation("skew_s", ["b", "c"], rows)
}

/// T(a,c): only five pairs survive the intersection, deliberately sparse.
fn skew_t() -> RelationSpec {
    // (1002,9002) is the one non-hub survivor (b=2 in both R and S).
    int_relation(
        "skew_t",
        ["a", "c"],
        vec![[7, 7], [42, 42], [100, 100], [200, 200], [1002, 9002]],
    )
}

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

fn write_fixture(spec: &RelationSpec) -> Result<(), ServiceError> {
    let dir = fixtures_dir();
    fs::create_dir_all(&dir).map_err(|e| {
        ServiceError::new(
            leapfrog_triejoin::ErrorCode::Internal,
            format!("create {:?}: {e}", dir),
        )
    })?;
    let path = dir.join(format!("{}.json", spec.name));
    let json = serde_json::to_string_pretty(spec).map_err(|e| {
        ServiceError::new(
            leapfrog_triejoin::ErrorCode::Internal,
            format!("serialize {}: {e}", spec.name),
        )
    })?;
    fs::write(&path, format!("{json}\n")).map_err(|e| {
        ServiceError::new(
            leapfrog_triejoin::ErrorCode::Internal,
            format!("write {:?}: {e}", path),
        )
    })?;
    println!("wrote {:?} ({} rows)", path, spec.rows.len());
    Ok(())
}

fn main() -> Result<(), ServiceError> {
    let edges = complete_graph_edges(5);
    assert_eq!(edges.len(), 10, "K5 has ten oriented edges");

    let triangle_r = int_relation("triangle_r", ["a", "b"], edges.clone());
    let triangle_s = int_relation("triangle_s", ["b", "c"], edges.clone());
    let triangle_t = int_relation("triangle_t", ["a", "c"], edges);

    for spec in [
        triangle_r,
        triangle_s,
        triangle_t,
        skew_r(),
        skew_s(),
        skew_t(),
    ] {
        write_fixture(&spec)?;
    }
    println!("all fixtures regenerated under {:?}", fixtures_dir());
    Ok(())
}
