//! Query planning and validation for restricted natural joins.
//!
//! A [`Plan`] is produced only after every static check succeeds:
//!
//! 1. relation/column names exist and are unambiguous;
//! 2. same-named columns have identical types across relations;
//! 3. the relation hypergraph is **connected** — a natural join with no
//!    shared column between components would be an unrestricted Cartesian
//!    product, which this backend refuses rather than computing by accident;
//! 4. the shape is inside the *restricted* envelope (2..=max relations,
//!    non-empty arity);
//! 5. NULL policy on join-key cells is applied explicitly.
//!
//! The planner also fixes the global variable (attribute) order used by the
//! Leapfrog engine. Every atom's Trie is then built with its columns in the
//! restriction of that order, which is the precondition for the trie-join
//! navigation invariant.

use std::collections::{HashMap, HashSet};

use crate::error::{ErrorCode, Result, ServiceError};
use crate::schema::{ColumnType, Relation};
use crate::value::Cell;

/// What happens when a join-key cell is NULL.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum NullPolicy {
    /// Reject the request up front ([`ErrorCode::NullKey`]). Default: silent
    /// row loss has caused real incidents, so we make it loud.
    #[default]
    Reject,
    /// SQL semantics: NULL never equals anything (not even NULL); rows
    /// carrying a NULL in any shared column are excluded from the join.
    SqlMatchNever,
}

impl NullPolicy {
    pub fn parse(s: &str) -> Result<Self> {
        match s {
            "reject" | "strict" => Ok(NullPolicy::Reject),
            "sql_match_never" | "drop" | "sql" => Ok(NullPolicy::SqlMatchNever),
            other => Err(ServiceError::new(
                ErrorCode::InvalidRequest,
                format!("unknown null_policy '{other}'"),
            )),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            NullPolicy::Reject => "reject",
            NullPolicy::SqlMatchNever => "sql_match_never",
        }
    }
}

/// One atom = one relation bound to global variable positions.
#[derive(Debug, Clone)]
pub struct Atom {
    pub relation_index: usize,
    /// `(global variable index, source column index)`, in global order.
    pub bindings: Vec<(usize, usize)>,
}

#[derive(Debug, Clone)]
pub struct Plan {
    pub relations: Vec<Relation>,
    /// Output attributes in first-occurrence order (stable, user-facing).
    pub output_columns: Vec<(String, ColumnType)>,
    /// Global binding order used by the engine (a permutation of outputs).
    pub variable_order: Vec<String>,
    /// Global position for each output attribute.
    pub global_index: HashMap<String, usize>,
    pub atoms: Vec<Atom>,
    /// Attributes shared by two or more relations.
    pub join_attributes: HashSet<String>,
    pub null_policy: NullPolicy,
}

impl Plan {
    /// Validate and plan a natural join over `relations`.
    pub fn natural_join(
        relations: Vec<Relation>,
        null_policy: NullPolicy,
        max_relations: usize,
    ) -> Result<Self> {
        if relations.len() < 2 {
            return Err(ServiceError::new(
                ErrorCode::InvalidRequest,
                "natural join needs at least 2 relations",
            ));
        }
        if relations.len() > max_relations {
            return Err(ServiceError::new(
                ErrorCode::UnsupportedShape,
                format!(
                    "join has {} relations; restricted envelope allows at most {max_relations}",
                    relations.len()
                ),
            ));
        }

        let mut seen_names: HashSet<&str> = HashSet::new();
        for rel in &relations {
            if !seen_names.insert(rel.name.as_str()) {
                return Err(ServiceError::new(
                    ErrorCode::InvalidRequest,
                    format!("relation '{}' is listed more than once", rel.name),
                )
                .with_field(format!("relations.{}", rel.name)));
            }
            rel.check_unique_columns()?;
            if rel.columns.is_empty() {
                return Err(ServiceError::new(
                    ErrorCode::InvalidRequest,
                    format!("relation '{}' has zero columns", rel.name),
                ));
            }
        }

        let output_columns = collect_output_columns(&relations)?;
        let occurrences = attribute_occurrences(&relations, &output_columns);
        let join_attributes: HashSet<String> = occurrences
            .iter()
            .filter(|(_, occ)| occ.len() >= 2)
            .map(|(name, _)| name.clone())
            .collect();

        if join_attributes.is_empty() {
            return Err(ServiceError::new(
                ErrorCode::DisjointSchema,
                "relations share no column names; natural join would be a Cartesian product",
            ));
        }

        check_connected(&relations, &join_attributes)?;
        apply_null_policy(&relations, &join_attributes, null_policy)?;

        let variable_order = choose_variable_order(&relations, &output_columns, &occurrences);
        let global_index: HashMap<String, usize> = variable_order
            .iter()
            .enumerate()
            .map(|(i, name)| (name.clone(), i))
            .collect();

        let atoms = relations
            .iter()
            .enumerate()
            .map(|(relation_index, rel)| {
                let mut bindings: Vec<(usize, usize)> = rel
                    .columns
                    .iter()
                    .enumerate()
                    .map(|(col_idx, col)| (global_index[&col.name], col_idx))
                    .collect();
                // Restrict the global order to this atom's attributes.
                bindings.sort_unstable_by_key(|(g, _)| *g);
                Atom {
                    relation_index,
                    bindings,
                }
            })
            .collect();

        Ok(Plan {
            relations,
            output_columns,
            variable_order,
            global_index,
            atoms,
            join_attributes,
            null_policy,
        })
    }

    pub fn arity(&self) -> usize {
        self.output_columns.len()
    }

    /// Rows surviving the NULL policy, in each relation's original row order;
    /// returned per relation. Under [`NullPolicy::Reject`] this is reached
    /// only after validation proved there is nothing to filter.
    pub fn rows_for_relation(&self, relation_index: usize) -> Vec<&Vec<Cell>> {
        let rel = &self.relations[relation_index];
        match self.null_policy {
            NullPolicy::Reject => rel.rows.iter().collect(),
            NullPolicy::SqlMatchNever => rel
                .rows
                .iter()
                .filter(|row| {
                    !rel.columns.iter().enumerate().any(|(c, col)| {
                        self.join_attributes.contains(&col.name) && matches!(row[c], Cell::Null)
                    })
                })
                .collect(),
        }
    }
}

/// Union of columns in first-occurrence order, rejecting type mismatches.
fn collect_output_columns(relations: &[Relation]) -> Result<Vec<(String, ColumnType)>> {
    let mut types: HashMap<&str, ColumnType> = HashMap::new();
    let mut order: Vec<(String, ColumnType)> = Vec::new();

    for rel in relations {
        for col in &rel.columns {
            match types.get(col.name.as_str()) {
                Some(existing) if *existing != col.typ => {
                    return Err(ServiceError::new(
                        ErrorCode::TypeMismatch,
                        format!(
                            "column '{}' is {} in '{}' but {} in '{}'",
                            col.name,
                            existing.as_str(),
                            first_relation_with_column(relations, &col.name).unwrap_or(&rel.name),
                            col.typ.as_str(),
                            rel.name
                        ),
                    ));
                }
                Some(_) => {}
                None => {
                    types.insert(col.name.as_str(), col.typ);
                    order.push((col.name.clone(), col.typ));
                }
            }
        }
    }
    Ok(order)
}

fn first_relation_with_column<'a>(relations: &'a [Relation], column: &str) -> Option<&'a String> {
    relations
        .iter()
        .find(|r| r.columns.iter().any(|c| c.name == column))
        .map(|r| &r.name)
}

/// For each output attribute, the relations that contain it.
fn attribute_occurrences(
    relations: &[Relation],
    output_columns: &[(String, ColumnType)],
) -> HashMap<String, Vec<usize>> {
    let mut occ: HashMap<String, Vec<usize>> = output_columns
        .iter()
        .map(|(n, _)| (n.clone(), Vec::new()))
        .collect();
    for (i, rel) in relations.iter().enumerate() {
        for col in &rel.columns {
            occ.get_mut(&col.name).expect("column collected").push(i);
        }
    }
    occ
}

/// Hypergraph connectivity: relations linked when they share a join column.
fn check_connected(relations: &[Relation], join_attributes: &HashSet<String>) -> Result<()> {
    // Attribute -> indices of relations containing it.
    let mut by_attr: HashMap<&str, Vec<usize>> = HashMap::new();
    for (i, rel) in relations.iter().enumerate() {
        for col in &rel.columns {
            if join_attributes.contains(&col.name) {
                by_attr.entry(col.name.as_str()).or_default().push(i);
            }
        }
    }

    let mut adj = vec![Vec::new(); relations.len()];
    for members in by_attr.values() {
        for w in members.windows(2) {
            adj[w[0]].push(w[1]);
            adj[w[1]].push(w[0]);
        }
    }

    let mut seen = vec![false; relations.len()];
    let mut stack = vec![0usize];
    seen[0] = true;
    while let Some(n) = stack.pop() {
        for &m in &adj[n] {
            if !seen[m] {
                seen[m] = true;
                stack.push(m);
            }
        }
    }

    if let Some(missing) = seen.iter().position(|s| !*s) {
        return Err(ServiceError::new(
            ErrorCode::UnsupportedShape,
            format!(
                "relation '{}' is not connected to the rest through shared columns; \
                 joining it would introduce a Cartesian product",
                relations[missing].name
            ),
        ));
    }
    Ok(())
}

/// Apply the key-NULL policy by scanning all shared-column cells.
fn apply_null_policy(
    relations: &[Relation],
    join_attributes: &HashSet<String>,
    policy: NullPolicy,
) -> Result<()> {
    for rel in relations {
        for (row_idx, row) in rel.rows.iter().enumerate() {
            for (col_idx, col) in rel.columns.iter().enumerate() {
                if join_attributes.contains(&col.name) && matches!(row[col_idx], Cell::Null) {
                    match policy {
                        NullPolicy::Reject => {
                            return Err(ServiceError::new(
                                ErrorCode::NullKey,
                                format!(
                                    "relation '{}' row {row_idx} has NULL in join key '{}'; \
                                     null_policy=reject forbids unmatched keys (use \
                                     null_policy=sql_match_never to exclude such rows)",
                                    rel.name, col.name
                                ),
                            )
                            .with_field(format!("relations.{}.rows", rel.name)));
                        }
                        NullPolicy::SqlMatchNever => {} // rows are filtered at execution.
                    }
                }
            }
        }
    }
    Ok(())
}

/// Greedy most-constrained-first variable order.
///
/// Score of an attribute is the largest distinct-value count of any relation
/// containing it (small = selective). We grow from the most selective
/// attribute and only add attributes reachable through a relation already
/// touched, which keeps trie navigation prefixes as tight as possible.
fn choose_variable_order(
    relations: &[Relation],
    output_columns: &[(String, ColumnType)],
    occurrences: &HashMap<String, Vec<usize>>,
) -> Vec<String> {
    let distinct: HashMap<String, usize> = output_columns
        .iter()
        .map(|(name, _)| {
            let mut max_distinct = 0usize;
            for &rel_idx in &occurrences[name] {
                let rel = &relations[rel_idx];
                let col_idx = rel.column_index(name).expect("occurrence has column");
                let mut set: HashSet<&Cell> = HashSet::new();
                for row in &rel.rows {
                    set.insert(&row[col_idx]);
                }
                max_distinct = max_distinct.max(set.len());
            }
            (name.clone(), max_distinct)
        })
        .collect();

    let mut remaining: HashSet<String> = output_columns.iter().map(|(n, _)| n.clone()).collect();
    let mut order: Vec<String> = Vec::new();

    let pick =
        |candidates: &HashSet<String>, order: &mut Vec<String>, remaining: &mut HashSet<String>| {
            let mut best: Option<(usize, &str)> = None;
            for name in candidates {
                let score = distinct[name];
                let better = match best {
                    None => true,
                    Some((s, n)) => (score, name.as_str()) < (s, n),
                };
                if better {
                    best = Some((score, name.as_str()));
                }
            }
            if let Some((_, name)) = best {
                let name = name.to_string();
                remaining.remove(&name);
                order.push(name);
                true
            } else {
                false
            }
        };

    // Seed with the globally most selective attribute.
    let all: HashSet<String> = remaining.clone();
    pick(&all, &mut order, &mut remaining);

    // Expand only across relations already connected to the chosen set.
    while !remaining.is_empty() {
        let chosen_rels = relations_touching(&order, relations);
        let candidates: HashSet<String> = remaining
            .iter()
            .filter(|name| occurrences[*name].iter().any(|ri| chosen_rels.contains(ri)))
            .cloned()
            .collect();

        if !pick(&candidates, &mut order, &mut remaining) {
            // Defensive: connectivity check should make this unreachable.
            let rest: HashSet<String> = remaining.clone();
            pick(&rest, &mut order, &mut remaining);
        }
    }

    order
}

fn relations_touching(attrs: &[String], relations: &[Relation]) -> HashSet<usize> {
    let attr: HashSet<&str> = attrs.iter().map(|s| s.as_str()).collect();
    relations
        .iter()
        .enumerate()
        .filter(|(_, rel)| rel.columns.iter().any(|c| attr.contains(c.name.as_str())))
        .map(|(i, _)| i)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{Column, Relation};
    use crate::value::Cell;

    fn int_rel(name: &str, cols: &[&str], rows: Vec<Vec<i64>>) -> Relation {
        let mut rel = Relation::new(
            name,
            cols.iter()
                .map(|c| Column::new(*c, ColumnType::Int))
                .collect(),
        );
        rel.set_rows(
            rows.into_iter()
                .map(|r| r.into_iter().map(Cell::from).collect())
                .collect(),
        )
        .unwrap();
        rel
    }

    #[test]
    fn rejects_disjoint_and_oversized_shapes() {
        let r = int_rel("r", &["a"], vec![vec![1]]);
        let s = int_rel("s", &["b"], vec![vec![1]]);
        let err = Plan::natural_join(vec![r, s], NullPolicy::Reject, 8).unwrap_err();
        assert_eq!(err.code, ErrorCode::DisjointSchema);

        let rels: Vec<Relation> = (0..9)
            .map(|i| int_rel(&format!("r{i}"), &["a"], vec![vec![1]]))
            .collect();
        let err = Plan::natural_join(rels, NullPolicy::Reject, 8).unwrap_err();
        assert_eq!(err.code, ErrorCode::UnsupportedShape);
    }

    #[test]
    fn rejects_type_mismatch_on_shared_column() {
        let mut r = Relation::new("r", vec![Column::new("a", ColumnType::Int)]);
        r.set_rows(vec![vec![Cell::from(1i64)]]).unwrap();
        let mut s = Relation::new("s", vec![Column::new("a", ColumnType::Str)]);
        s.set_rows(vec![vec![Cell::from("x")]]).unwrap();
        let err = Plan::natural_join(vec![r, s], NullPolicy::Reject, 8).unwrap_err();
        assert_eq!(err.code, ErrorCode::TypeMismatch);
    }

    #[test]
    fn null_policies_are_distinguished() {
        let mk = |rows_r: Vec<Vec<Cell>>| {
            let mut r = Relation::new(
                "r",
                vec![
                    Column::new("a", ColumnType::Int),
                    Column::new("x", ColumnType::Int),
                ],
            );
            r.set_rows(rows_r).unwrap();
            let mut s = Relation::new("s", vec![Column::new("a", ColumnType::Int)]);
            s.set_rows(vec![vec![Cell::from(1i64)]]).unwrap();
            (r, s)
        };

        let (r1, s1) = mk(vec![vec![Cell::Null, Cell::from(9i64)]]);
        let err = Plan::natural_join(vec![r1, s1], NullPolicy::Reject, 8).unwrap_err();
        assert_eq!(err.code, ErrorCode::NullKey);

        let (r2, s2) = mk(vec![
            vec![Cell::Null, Cell::from(9i64)],
            vec![Cell::from(1i64), Cell::from(7i64)],
        ]);
        let plan = Plan::natural_join(vec![r2, s2], NullPolicy::SqlMatchNever, 8).unwrap();
        let rows = plan.rows_for_relation(0);
        assert_eq!(rows.len(), 1, "NULL-key row must be excluded");
    }
}
