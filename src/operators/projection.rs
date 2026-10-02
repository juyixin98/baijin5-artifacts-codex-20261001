//! Streaming column projection (select / reorder). Fully pipelined: it pulls
//! one batch, projects it, and passes it on without blocking.

use std::sync::Arc;

use crate::batch::{Batch, Schema};
use crate::cancel::Control;
use crate::error::{QueryError, QueryResult};
use crate::operator::{Operator, OperatorCore};

/// Generic unary wrapper holding one child. Shared by streaming operators.
pub struct Unary {
    pub(crate) core: OperatorCore,
    pub child: Box<dyn Operator>,
}

impl Unary {
    pub fn new(name: impl Into<String>, child: Box<dyn Operator>) -> Self {
        Self {
            core: OperatorCore::new(name),
            child,
        }
    }
}

/// Column projection operator.
pub struct Projection {
    base: Unary,
    out_schema: Arc<Schema>,
    indices: Vec<usize>,
}

impl Projection {
    /// Project the child's output to `columns` (by name), validating up front.
    pub fn new(
        name: impl Into<String>,
        child: Box<dyn Operator>,
        columns: &[String],
    ) -> QueryResult<Self> {
        let in_schema = child.schema_out();
        let mut indices = Vec::with_capacity(columns.len());
        for c in columns {
            let idx = in_schema.index_of(c).ok_or_else(|| {
                QueryError::invalid_input(format!(
                    "projection references unknown column {c:?}; have {:?}",
                    in_schema
                        .fields()
                        .iter()
                        .map(|(n, _)| n)
                        .collect::<Vec<_>>()
                ))
            })?;
            indices.push(idx);
        }
        let out_schema = Arc::new(in_schema.project(&indices)?);
        Ok(Self {
            base: Unary::new(name, child),
            out_schema,
            indices,
        })
    }

    pub fn indices(&self) -> &[usize] {
        &self.indices
    }
}

impl Operator for Projection {
    fn name(&self) -> &str {
        self.base.core.name()
    }
    fn schema_out(&self) -> Arc<Schema> {
        self.out_schema.clone()
    }
    fn core(&self) -> &OperatorCore {
        &self.base.core
    }
    fn core_mut(&mut self) -> &mut OperatorCore {
        &mut self.base.core
    }
    fn pull(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>> {
        match self.base.child.next(ctrl)? {
            Some(batch) => Ok(Some(batch.project(&self.indices)?)),
            None => Ok(None),
        }
    }
    fn release(&mut self, ctrl: Option<&Control>) {
        // Propagate close to the child exactly once. The wrapper's own
        // released-flag guarantees this runs at most once.
        self.base.child.shutdown(ctrl);
    }
}
