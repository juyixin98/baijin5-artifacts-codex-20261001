//! Bundled synthetic fixtures for the server binary.
//!
//! Files live under `fixtures/` and are embedded at compile time, so the
//! running server needs no external data and no production accounts. The
//! on-disk files are produced reproducibly by
//! `cargo run --example generate_fixtures`.

use leapfrog_triejoin::error::Result;
use leapfrog_triejoin::state::Catalog;
use leapfrog_triejoin::validate::{load_relations, RelationSpec};

/// One embedded (file name, contents) pair.
const BUNDLED: &[(&str, &str)] = &[
    // Triangle query over the complete graph K5 (oriented edges i<j).
    (
        "triangle_r",
        include_str!("../../../fixtures/triangle_r.json"),
    ),
    (
        "triangle_s",
        include_str!("../../../fixtures/triangle_s.json"),
    ),
    (
        "triangle_t",
        include_str!("../../../fixtures/triangle_t.json"),
    ),
    // Highly-skewed hub join with a sparse intersecting edge.
    ("skew_r", include_str!("../../../fixtures/skew_r.json")),
    ("skew_s", include_str!("../../../fixtures/skew_s.json")),
    ("skew_t", include_str!("../../../fixtures/skew_t.json")),
];

/// Parse and register every bundled relation. Returns the count.
pub fn load_bundled(catalog: &mut Catalog) -> Result<usize> {
    let mut specs = Vec::with_capacity(BUNDLED.len());
    for (label, contents) in BUNDLED {
        let spec: RelationSpec = serde_json::from_str(contents).map_err(|e| {
            leapfrog_triejoin::ServiceError::new(
                leapfrog_triejoin::ErrorCode::Internal,
                format!("bundled fixture {label} is invalid: {e}"),
            )
        })?;
        specs.push(spec);
    }
    load_relations(catalog, specs)?;
    Ok(BUNDLED.len())
}
