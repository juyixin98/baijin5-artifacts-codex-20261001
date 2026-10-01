//! Working rows: the rows the recursive term expands *next*.
//!
//! The semi-naïve contract is enforced here: a [`WorkingTable`] only ever
//! contains rows produced by the previous expansion that were genuinely new
//! (set-dedup happens in [`super::accumulated::Accumulated`] before insertion).
//! Rows already accumulated never re-enter the working table.

use crate::batch::Value;

/// One row awaiting expansion.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkingRow {
    /// Business-column values in CTE schema order.
    pub business: Vec<Value>,
    /// Declared key values from the seed down to this row.
    /// Each element is one node's composite key tuple; a single-column key
    /// is a one-element tuple. The path column renders the flattened form.
    pub path: Vec<Vec<Value>>,
    /// Whether this row closes back onto one of its ancestors.
    /// Cycle rows are emitted but **never expanded**.
    pub cycle: bool,
    /// Seed rows live at depth 0; each expansion adds one.
    pub depth: u32,
}

impl WorkingRow {
    /// Build a seed row (depth 0, path seeded with its own key, never a cycle).
    pub fn seed(business: Vec<Value>, key: Vec<Value>) -> Self {
        Self {
            business,
            path: vec![key],
            cycle: false,
            depth: 0,
        }
    }

    /// The full output row: business values + rendered path + cycle flag.
    pub fn output_row(&self, path_column: &str) -> Vec<Value> {
        let _ = path_column; // name is fixed by the output schema; kept for call-site clarity
        let flat: Vec<Value> = self.path.iter().flatten().cloned().collect();
        let mut row = self.business.clone();
        row.push(Value::Utf8(Value::render_path(&flat)));
        row.push(Value::Boolean(self.cycle));
        row
    }

    /// This row's own declared key tuple.
    pub fn key(&self, key_indices: &[usize]) -> Vec<Value> {
        key_indices
            .iter()
            .map(|&i| self.business[i].clone())
            .collect()
    }
}

/// Container of rows to expand in the current round.
#[derive(Debug, Default)]
pub struct WorkingTable {
    rows: Vec<WorkingRow>,
}

impl WorkingTable {
    /// Empty working table.
    pub fn new() -> Self {
        Self { rows: Vec::new() }
    }

    /// Seed the table with depth-0 rows.
    pub fn from_seeds(rows: Vec<WorkingRow>) -> Self {
        Self { rows }
    }

    /// Number of rows awaiting expansion.
    pub fn len(&self) -> usize {
        self.rows.len()
    }

    /// Whether anything remains to expand.
    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    /// Drain every row, leaving the table empty.
    pub fn drain(&mut self) -> Vec<WorkingRow> {
        std::mem::take(&mut self.rows)
    }

    /// Peek at the contained rows (tests / traces).
    pub fn rows(&self) -> &[WorkingRow] {
        &self.rows
    }

    /// Replace contents with exactly the newly produced rows for next round.
    pub fn set_next(&mut self, rows: Vec<WorkingRow>) {
        self.rows = rows;
    }

    /// Pop one row (LIFO — used by depth-first traversal).
    pub fn pop(&mut self) -> Option<WorkingRow> {
        self.rows.pop()
    }

    /// Push one row onto the end (LIFO stack semantics with [`Self::pop`]).
    pub fn push(&mut self, row: WorkingRow) {
        self.rows.push(row);
    }
}
