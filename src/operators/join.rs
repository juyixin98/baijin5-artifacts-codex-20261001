//! Blocking-build / streaming-probe hash equi-join.
//!
//! On the first pull the entire **right (build) side** is drained into an
//! in-memory hash table keyed by one or more equi-join columns; afterwards left
//! (probe) batches stream through. This is a classic pull-tree binary operator:
//! right is a blocking dependency, left is pipelined.
//!
//! Nulls never match (SQL semantics): a null key on either side produces no
//! join row. Key column types must agree across the two sides; a mismatch is
//! rejected at construction as [`ErrorKind::InvalidInput`], while a per-row key
//! representation problem surfaces as [`ErrorKind::ComputationFailed`].
//!
//! Resources (the hash table) are released exactly once by [`release`], which
//! also shuts both children down. Build can be cancelled/timed-out because
//! [`Control::check`] runs before each right-side pull.

use std::collections::HashMap;
use std::sync::Arc;

use crate::batch::{Batch, Scalar, Schema};
use crate::cancel::Control;
use crate::error::{QueryError, QueryResult};
use crate::operator::{Operator, OperatorCore};
use crate::operators::{batch_rows, rows_to_batch};

/// Join type. Only inner is required, but left-presence is modeled for clarity.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JoinType {
    Inner,
}

pub struct HashJoin {
    core: OperatorCore,
    left: Box<dyn Operator>,
    right: Box<dyn Operator>,
    left_keys: Vec<usize>,
    right_keys: Vec<usize>,
    out_schema: Arc<Schema>,
    join_type: JoinType,
    state: JoinState,
}

enum JoinState {
    /// Right side not yet built.
    Unbuilt,
    /// Build done; probe streaming.
    Probing {
        table: HashMap<JoinKey, Vec<Vec<Scalar>>>,
        right_rows: u64,
    },
    /// Right side was empty: emit nothing (inner join).
    Empty,
}

/// A multi-column join key. Nulls are represented as absent and never match.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct JoinKey(Vec<KeyValue>);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum KeyValue {
    Int(i64),
    Utf8(String),
    Bool(bool),
}

fn key_values(row: &[Scalar], cols: &[usize]) -> Option<Vec<KeyValue>> {
    let mut out = Vec::with_capacity(cols.len());
    for &c in cols {
        match row.get(c)? {
            Scalar::Int(None) | Scalar::Utf8(None) | Scalar::Bool(None) => return None,
            Scalar::Int(Some(v)) => out.push(KeyValue::Int(*v)),
            Scalar::Utf8(Some(v)) => out.push(KeyValue::Utf8(v.clone())),
            Scalar::Bool(Some(v)) => out.push(KeyValue::Bool(*v)),
        }
    }
    Some(out)
}

impl HashJoin {
    pub fn new(
        name: impl Into<String>,
        left: Box<dyn Operator>,
        right: Box<dyn Operator>,
        left_key_names: &[String],
        right_key_names: &[String],
        join_type: JoinType,
    ) -> QueryResult<Self> {
        if left_key_names.len() != right_key_names.len() || left_key_names.is_empty() {
            return Err(QueryError::invalid_input(
                "join requires equal, non-empty key lists on both sides",
            ));
        }
        let ls = left.schema_out();
        let rs = right.schema_out();

        let mut left_keys = Vec::new();
        let mut right_keys = Vec::new();
        for (ln, rn) in left_key_names.iter().zip(right_key_names.iter()) {
            let li = ls.index_of(ln).ok_or_else(|| {
                QueryError::invalid_input(format!("left join key {ln:?} not found"))
            })?;
            let ri = rs.index_of(rn).ok_or_else(|| {
                QueryError::invalid_input(format!("right join key {rn:?} not found"))
            })?;
            if ls.fields()[li].1 != rs.fields()[ri].1 {
                return Err(QueryError::invalid_input(format!(
                    "join key type mismatch: left {ln:?}:{} vs right {rn:?}:{}",
                    ls.fields()[li].1.as_str(),
                    rs.fields()[ri].1.as_str()
                )));
            }
            left_keys.push(li);
            right_keys.push(ri);
        }

        // Output schema = left columns followed by right columns.
        let mut fields = ls.fields().to_vec();
        fields.extend(rs.fields().iter().cloned());
        let out_schema = Arc::new(Schema::new(fields));

        Ok(Self {
            core: OperatorCore::new(name),
            left,
            right,
            left_keys,
            right_keys,
            out_schema,
            join_type,
            state: JoinState::Unbuilt,
        })
    }

    pub fn join_type(&self) -> JoinType {
        self.join_type
    }

    /// Number of build-side rows consumed (0 before build).
    pub fn build_rows(&self) -> u64 {
        match &self.state {
            JoinState::Probing { right_rows, .. } => *right_rows,
            _ => 0,
        }
    }

    /// Drain the right side into the hash table, with control safe points.
    fn build(&mut self, ctrl: &Control) -> QueryResult<()> {
        let name = self.name().to_string();
        let mut table: HashMap<JoinKey, Vec<Vec<Scalar>>> = HashMap::new();
        let mut right_rows = 0u64;
        loop {
            ctrl.check()?;
            let batch = match self.right.next(ctrl) {
                Ok(Some(b)) => b,
                Ok(None) => break,
                // Do not keep pulling a damaged build stream.
                Err(e) => return Err(e),
            };
            let rows = batch_rows(&batch)?;
            for row in rows {
                right_rows += 1;
                if let Some(kv) = key_values(&row, &self.right_keys) {
                    table.entry(JoinKey(kv)).or_default().push(row.clone());
                }
                // Null-key right rows are simply not inserted (never match).
            }
        }
        ctrl.diag().info(
            &name,
            "built",
            format!("right_rows={right_rows} distinct_keys={}", table.len()),
        );
        if table.is_empty() && right_rows == 0 {
            self.state = JoinState::Empty;
        } else {
            self.state = JoinState::Probing { table, right_rows };
        }
        Ok(())
    }
}

impl Operator for HashJoin {
    fn name(&self) -> &str {
        self.core.name()
    }
    fn schema_out(&self) -> Arc<Schema> {
        self.out_schema.clone()
    }
    fn core(&self) -> &OperatorCore {
        &self.core
    }
    fn core_mut(&mut self) -> &mut OperatorCore {
        &mut self.core
    }

    fn pull(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>> {
        if matches!(self.state, JoinState::Unbuilt) {
            self.build(ctrl)?;
        }
        if matches!(self.state, JoinState::Empty) {
            return Ok(None);
        }

        loop {
            ctrl.check()?;
            let batch = match self.left.next(ctrl) {
                Ok(Some(b)) => b,
                Ok(None) => return Ok(None),
                Err(e) => return Err(e),
            };

            let left_rows = batch_rows(&batch)?;
            let mut out: Vec<Vec<Scalar>> = Vec::new();

            // We need per-column access to validate key representations against
            // the declared types (defensive: batch already type-checked, but a
            // join must fail ComputationFailed rather than panic on a bad key).
            for lrow in &left_rows {
                let key = match key_values_checked(lrow, &self.left_keys)? {
                    Some(k) => k,
                    None => continue, // null probe key → no match (inner join)
                };
                if let JoinState::Probing { table, .. } = &self.state {
                    if let Some(matches) = table.get(&JoinKey(key)) {
                        for rrow in matches {
                            let mut joined = lrow.clone();
                            joined.extend_from_slice(rrow);
                            out.push(joined);
                        }
                    }
                }
            }

            if !out.is_empty() {
                return Ok(Some(rows_to_batch(self.out_schema.clone(), &out)?));
            }
            // Entire probe batch matched nothing; try the next left batch.
        }
    }

    fn release(&mut self, ctrl: Option<&Control>) {
        // Drop the hash table, then close both children exactly once.
        self.state = JoinState::Empty;
        self.right.shutdown(ctrl);
        self.left.shutdown(ctrl);
    }
}

/// Build a key while verifying every key column's scalar variant matches the
/// left schema's declared type; a mismatch is a computation failure (it should
/// be impossible given batch validation, but a join must not panic on it).
fn key_values_checked(row: &[Scalar], cols: &[usize]) -> QueryResult<Option<Vec<KeyValue>>> {
    let mut out = Vec::with_capacity(cols.len());
    for &c in cols {
        let scalar = row
            .get(c)
            .ok_or_else(|| QueryError::computation("join key index past row width"))?;
        match scalar {
            Scalar::Int(None) | Scalar::Utf8(None) | Scalar::Bool(None) => return Ok(None),
            Scalar::Int(Some(v)) => out.push(KeyValue::Int(*v)),
            Scalar::Utf8(Some(v)) => out.push(KeyValue::Utf8(v.clone())),
            Scalar::Bool(Some(v)) => out.push(KeyValue::Bool(*v)),
        }
    }
    Ok(Some(out))
}
