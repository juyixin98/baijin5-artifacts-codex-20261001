//! Seed assembly: expand the user-provided seed into full view-width rows,
//! initializing engine-managed path/cycle columns.

use crate::batch::{Scalar, TypedBatch};
use crate::error::{EngineError, EngineResult};
use crate::plan::{ColumnDecl, CycleConfig, RecursiveRequest, Relation};

/// Resolved positions of the managed columns, if any.
#[derive(Debug, Clone, Copy)]
pub struct ManagedPositions {
    pub key: usize,
    pub path: usize,
    pub cycle: usize,
}

/// Resolve managed-column positions against the view schema.
pub fn resolve_positions(req: &RecursiveRequest) -> Option<(usize, usize, usize)> {
    let cyc = req.cycle.as_ref()?;
    let find = |name: &str| req.view.columns.iter().position(|c| c.name == name);
    match (
        find(&cyc.key_column),
        find(&cyc.path_column),
        find(&cyc.cycle_column),
    ) {
        (Some(k), Some(p), Some(c)) => Some((k, p, c)),
        _ => None,
    }
}

/// The result schema is always the full declared view schema.
pub fn result_schema(req: &RecursiveRequest) -> Vec<ColumnDecl> {
    req.view.columns.clone()
}

/// Build the seed [`TypedBatch`] (edge relations are parsed separately by the
/// caller) and produce seed rows already laid out in full view order.
pub fn assemble_seed_rows(
    req: &RecursiveRequest,
    positions: Option<ManagedPositions>,
) -> EngineResult<Vec<Vec<Scalar>>> {
    // User-provided seed columns: all view columns except managed ones, in
    // view order.
    let managed: &CycleConfig = match req.cycle.as_ref() {
        Some(c) => c,
        None => {
            // No managed columns: parse seed exactly as declared.
            let batch = TypedBatch::from_relation(&req.view)?;
            return Ok(batch.rows().to_vec());
        }
    };

    let seed_columns = req
        .view
        .columns
        .iter()
        .filter(|c| c.name != managed.path_column && c.name != managed.cycle_column)
        .cloned()
        .collect::<Vec<_>>();
    let seed_relation = Relation {
        columns: seed_columns.clone(),
        rows: req.view.rows.clone(),
    };
    let seed_batch = TypedBatch::from_relation(&seed_relation)?;

    let pos = positions.expect("positions present whenever cycle config is present");
    let width = req.view.columns.len();
    let mut rows = Vec::with_capacity(seed_batch.row_count());
    for (i, narrow) in seed_batch.rows().iter().enumerate() {
        let mut full = vec![Scalar::Null; width];

        // Place user values at their view positions.
        for (decl, value) in seed_columns.iter().zip(narrow) {
            let view_pos = req
                .view
                .columns
                .iter()
                .position(|c| c.name == decl.name)
                .expect("seed column derives from view columns");
            full[view_pos] = value.clone();
        }

        // Managed columns: path starts as [key], marker starts false.
        match &full[pos.key] {
            Scalar::Null => {
                return Err(EngineError::invalid_data(format!(
                    "seed row {i}: cycle key column '{}' must not be null",
                    managed.key_column
                )))
            }
            Scalar::Int(k) => full[pos.path] = Scalar::IntList(vec![*k]),
            Scalar::Utf8(k) => full[pos.path] = Scalar::Utf8List(vec![k.clone()]),
            other => {
                return Err(EngineError::internal(format!(
                    "seed row {i}: unexpected key scalar {other:?}"
                )))
            }
        }
        full[pos.cycle] = Scalar::Bool(false);

        rows.push(full);
    }
    Ok(rows)
}

/// Extend a parent path scalar with the child key.
pub fn extend_path(parent_path: &Scalar, child_key: &Scalar) -> EngineResult<Scalar> {
    match (parent_path, child_key) {
        (Scalar::IntList(xs), Scalar::Int(k)) => {
            let mut next = xs.clone();
            next.push(*k);
            Ok(Scalar::IntList(next))
        }
        (Scalar::Utf8List(xs), Scalar::Utf8(k)) => {
            let mut next = xs.clone();
            next.push(k.clone());
            Ok(Scalar::Utf8List(next))
        }
        (_, Scalar::Null) => Err(EngineError::invalid_data(
            "recursive term produced a null cycle key; path extension is undefined",
        )),
        _ => Err(EngineError::internal(
            "path/key type mismatch during path extension",
        )),
    }
}
