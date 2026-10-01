//! Query model, compilation and validation.
//!
//! A [`JoinRequest`] is the untrusted boundary type. [`compile`] turns it into
//! a [`Plan`]: relations are built into ordered tries whose leading columns
//! follow one *global join-variable order*, and every safety restriction is
//! enforced here before the engine runs.
//!
//! Restricted multi-table natural join means exactly:
//! * every output attribute must be bound by some relation;
//! * shared attributes must share a type;
//! * the relation/attribute join graph must be **connected** — a disconnected
//!   graph is a Cartesian product and is rejected, never evaluated;
//! * NULL on a join key follows the explicit [`NullPolicy`].
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::batch::{ColumnSchema, RelationSchema, TypedBatch};
use crate::domain::{Datum, LogicalType, NullPolicy};
use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::trie::{SharedTrie, Trie};

/// Hard safety ceiling for a single restricted join. The paper's algorithm is
/// worst-case optimal at any arity; the cap exists to bound planner work and
/// diagnostics, not to paper over intermediate-result blowups (there are none).
pub const MAX_RELATIONS: usize = 16;

/// One inline relation in a request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelationInput {
    pub name: String,
    pub schema: Vec<ColumnSchema>,
    /// Row-major values; each row length matches `schema`.
    pub rows: Vec<Vec<Datum>>,
}

/// A full join request. Either inline `relations` are supplied, or server
/// catalog fixture names are referenced via `fixtures` (never both required).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JoinRequest {
    #[serde(default)]
    pub relations: Vec<RelationInput>,
    #[serde(default)]
    pub fixtures: Vec<String>,
    /// Output attributes in desired order; defaults to canonical order.
    #[serde(default)]
    pub select: Option<Vec<String>>,
    /// Maximum number of output tuples returned in this page.
    #[serde(default = "default_limit")]
    pub limit: u64,
    /// Opaque continuation token returned by a previous call.
    #[serde(default)]
    pub cursor: Option<String>,
    #[serde(default)]
    pub null_policy: NullPolicy,
    /// Caller correlation id; synthesized server-side when absent.
    #[serde(default)]
    pub request_id: Option<String>,
}

fn default_limit() -> u64 {
    100
}

impl JoinRequest {
    pub fn effective_limit(&self) -> u64 {
        if self.limit == 0 {
            default_limit()
        } else {
            self.limit
        }
    }
}

/// A relation after compilation: typed batch validated, trie built.
#[derive(Debug)]
pub struct PreparedRelation {
    pub name: String,
    pub schema: Arc<RelationSchema>,
    pub trie: SharedTrie,
    /// Attribute names in trie column order (join vars first, then private).
    pub attributes: Vec<String>,
    /// Indices (into [`PreparedRelation::attributes`]) of that relation's
    /// private columns.
    pub private: Vec<usize>,
    pub input_rows: usize,
    pub distinct_rows: usize,
    pub null_join_rows_dropped: usize,
}

/// Immutable, validated execution plan.
#[derive(Debug)]
pub struct Plan {
    pub relations: Vec<PreparedRelation>,
    /// Global join-variable order (attribute appears in >= 2 relations).
    pub join_vars: Vec<String>,
    /// owners[d]: relation indices that contain join variable d (>= 2).
    pub owners: Vec<Vec<usize>>,
    /// Canonical output order: join vars then private attrs grouped by
    /// relation (request order).
    pub output_attributes: Vec<String>,
    pub output_types: Vec<LogicalType>,
    /// What the caller asked to project, already resolved against
    /// `output_attributes`. Equals canonical order unless projection used.
    pub select: Vec<String>,
    pub null_policy: NullPolicy,
}

impl Plan {
    pub fn is_projection(&self) -> bool {
        self.select != self.output_attributes
    }
}

/// Merge inline relations and catalog-resolved fixture batches into one list.
pub fn resolve_inputs(
    req: &JoinRequest,
    catalog: &[(String, Arc<RelationSchema>, TypedBatch)],
) -> JoinResult<Vec<RelationInput>> {
    let mut out: Vec<RelationInput> = Vec::new();
    for name in &req.fixtures {
        let (n, schema, batch) = catalog.iter().find(|(n, _, _)| n == name).ok_or_else(|| {
            JoinError::new(
                ErrorCode::MissingRelation,
                format!("unknown catalog fixture '{name}'"),
            )
        })?;
        let rows = (0..batch.row_count())
            .map(|r| batch.projected_row(r, &(0..schema.arity()).collect::<Vec<_>>()))
            .collect();
        out.push(RelationInput {
            name: n.clone(),
            schema: schema.columns.clone(),
            rows,
        });
    }
    out.extend(req.relations.iter().cloned());
    if out.is_empty() {
        return Err(JoinError::new(
            ErrorCode::EmptyRelationList,
            "request must supply inline relations or catalog fixtures",
        ));
    }
    if out.len() > MAX_RELATIONS {
        return Err(JoinError::new(
            ErrorCode::TooManyRelations,
            format!(
                "at most {MAX_RELATIONS} relations allowed, got {}",
                out.len()
            ),
        ));
    }
    Ok(out)
}

/// Validate and compile a request into a [`Plan`].
pub fn compile(inputs: Vec<RelationInput>, req: &JoinRequest) -> JoinResult<Plan> {
    if req.effective_limit() > 1_000_000 {
        return Err(JoinError::new(
            ErrorCode::BadLimit,
            "limit must be <= 1,000,000",
        ));
    }

    // ---- Per-relation schema validation + type table ---------------------
    let mut schemas: Vec<Arc<RelationSchema>> = Vec::with_capacity(inputs.len());
    let mut batches: Vec<TypedBatch> = Vec::with_capacity(inputs.len());
    for rel in &inputs {
        if rel.schema.is_empty() {
            return Err(JoinError::new(
                ErrorCode::EmptySchema,
                format!("relation '{}' declares no columns", rel.name),
            ));
        }
        if rel.rows.is_empty() {
            return Err(JoinError::new(
                ErrorCode::EmptyRelation,
                format!("relation '{}' has zero rows", rel.name),
            ));
        }
        let mut seen = std::collections::HashSet::new();
        for c in &rel.schema {
            if !seen.insert(c.name.clone()) {
                return Err(JoinError::new(
                    ErrorCode::DuplicateColumn,
                    format!("relation '{}' repeats column '{}'", rel.name, c.name),
                ));
            }
        }
        let schema = Arc::new(RelationSchema::new(rel.schema.clone()));
        let batch = TypedBatch::from_rows(schema.clone(), rel.rows.clone())?;
        schemas.push(schema);
        batches.push(batch);
    }

    // ---- Attribute -> type compatibility across relations ----------------
    let mut attr_type: std::collections::BTreeMap<String, LogicalType> =
        std::collections::BTreeMap::new();
    let mut attr_rels: std::collections::BTreeMap<String, Vec<usize>> =
        std::collections::BTreeMap::new();
    let mut first_seen: Vec<String> = Vec::new();
    for (ri, schema) in schemas.iter().enumerate() {
        for col in &schema.columns {
            if let Some(&existing) = attr_type.get(&col.name) {
                if existing != col.ty {
                    return Err(JoinError::new(
                        ErrorCode::TypeMismatch,
                        format!(
                            "attribute '{}' has incompatible types {} vs {}",
                            col.name,
                            existing.as_str(),
                            col.ty.as_str()
                        ),
                    ));
                }
            } else {
                attr_type.insert(col.name.clone(), col.ty);
                first_seen.push(col.name.clone());
            }
            attr_rels.entry(col.name.clone()).or_default().push(ri);
        }
    }

    // ---- Join graph connectivity (reject Cartesian products) --------------
    if inputs.len() > 1 {
        ensure_connected(&inputs)?;
    }

    // ---- Join variables: attributes bound by >= 2 relations ---------------
    let join_vars: Vec<String> = first_seen
        .iter()
        .filter(|a| attr_rels[*a].len() >= 2)
        .cloned()
        .collect();
    if inputs.len() > 1 && join_vars.is_empty() {
        return Err(JoinError::new(
            ErrorCode::NoCommonAttribute,
            "relations share no attribute; natural join is undefined",
        ));
    }

    // ---- NULL policy on join keys -----------------------------------------
    let mut prepared: Vec<PreparedRelation> = Vec::with_capacity(inputs.len());
    for (ri, rel) in inputs.iter().enumerate() {
        let schema = schemas[ri].clone();
        let join_col_indices: Vec<usize> = join_vars
            .iter()
            .filter_map(|v| schema.index_of(v))
            .collect();
        let private_col_indices: Vec<usize> = (0..schema.arity())
            .filter(|ci| !join_col_indices.contains(ci))
            .collect();

        // Split rows on NULL in any join column.
        let mut kept = 0usize;
        let mut null_dropped = 0usize;
        let mut ordered_rows: Vec<Vec<Datum>> = Vec::with_capacity(batches[ri].row_count());
        for row in 0..batches[ri].row_count() {
            let has_null_join = join_col_indices
                .iter()
                .any(|&ci| batches[ri].column(ci)[row] == Datum::Null);
            if has_null_join {
                match req.null_policy {
                    NullPolicy::Reject => {
                        return Err(JoinError::new(
                            ErrorCode::NullInJoinKey,
                            format!(
                                "relation '{}' row {row} is NULL on a join column; \
                                 null_policy=reject forbids it",
                                rel.name
                            ),
                        ));
                    }
                    NullPolicy::DropJoinRows => {
                        null_dropped += 1;
                        continue;
                    }
                }
            }
            let mut order = join_col_indices.clone();
            order.extend_from_slice(&private_col_indices);
            ordered_rows.push(batches[ri].projected_row(row, &order));
            kept += 1;
        }
        if kept == 0 {
            return Err(JoinError::new(
                ErrorCode::EmptyRelation,
                format!(
                    "relation '{}' has no rows left after applying NULL policy",
                    rel.name
                ),
            ));
        }

        let mut attributes: Vec<String> = join_col_indices
            .iter()
            .map(|&ci| schema.columns[ci].name.clone())
            .collect();
        attributes.extend(
            private_col_indices
                .iter()
                .map(|&ci| schema.columns[ci].name.clone()),
        );
        let distinct_rows = ordered_rows.len();
        let trie = Arc::new(Trie::from_ordered_rows(
            (0..attributes.len()).collect(),
            ordered_rows,
        ));
        prepared.push(PreparedRelation {
            name: rel.name.clone(),
            schema,
            trie,
            attributes,
            private: (join_col_indices.len()..join_col_indices.len() + private_col_indices.len())
                .collect(),
            input_rows: batches[ri].row_count(),
            distinct_rows,
            null_join_rows_dropped: null_dropped,
        });
    }

    // ---- Owners per global join variable ----------------------------------
    let owners: Vec<Vec<usize>> = join_vars.iter().map(|v| attr_rels[v].clone()).collect();

    // ---- Canonical output attributes --------------------------------------
    let mut output_attributes = join_vars.clone();
    for (ri, prep) in prepared.iter().enumerate() {
        for attr in &prep.attributes {
            if !join_vars.contains(attr) && !output_attributes.contains(attr) {
                output_attributes.push(attr.clone());
            }
        }
        let _ = ri;
    }
    let output_types: Vec<LogicalType> = output_attributes.iter().map(|a| attr_type[a]).collect();

    // ---- Projection validation --------------------------------------------
    let select = match &req.select {
        None => output_attributes.clone(),
        Some(sel) => {
            if sel.is_empty() {
                return Err(JoinError::new(
                    ErrorCode::EmptySchema,
                    "select must list at least one attribute",
                ));
            }
            let mut seen = std::collections::HashSet::new();
            for a in sel {
                if !seen.insert(a) {
                    return Err(JoinError::new(
                        ErrorCode::DuplicateVariable,
                        format!("select repeats attribute '{a}'"),
                    ));
                }
                if !attr_type.contains_key(a) {
                    return Err(JoinError::new(
                        ErrorCode::VariableNotBound,
                        format!("select attribute '{a}' is not bound by any relation"),
                    ));
                }
            }
            sel.clone()
        }
    };

    Ok(Plan {
        relations: prepared,
        join_vars,
        owners,
        output_attributes,
        output_types,
        select,
        null_policy: req.null_policy,
    })
}

/// Union-find connectivity: relations are nodes, an attribute appearing in two
/// relations unions them. A disconnected graph means a dangling Cartesian
/// product.
fn ensure_connected(inputs: &[RelationInput]) -> JoinResult<()> {
    let n = inputs.len();
    let mut parent: Vec<usize> = (0..n).collect();
    fn find(parent: &mut [usize], x: usize) -> usize {
        if parent[x] != x {
            parent[x] = find(parent, parent[x]);
        }
        parent[x]
    }
    let mut seen: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    for (ri, rel) in inputs.iter().enumerate() {
        for col in &rel.schema {
            if let Some(&other) = seen.get(&col.name) {
                let (a, b) = (find(&mut parent, ri), find(&mut parent, other));
                if a != b {
                    parent[a] = b;
                }
            } else {
                seen.insert(col.name.clone(), ri);
            }
        }
    }
    let root = find(&mut parent, 0);
    for i in 1..n {
        if find(&mut parent, i) != root {
            return Err(JoinError::new(
                ErrorCode::DisconnectedJoinGraph,
                format!(
                    "relation '{}' shares no attribute with the connected component of '{}'; \
                     joining it would form a Cartesian product",
                    inputs[i].name, inputs[0].name
                ),
            ));
        }
    }
    Ok(())
}
