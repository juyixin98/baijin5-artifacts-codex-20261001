//! Typed batches built on Arrow2.
//!
//! An [`InputBatch`] is an Arrow2 `Chunk` of boxed arrays paired with a fixed
//! schema:
//!
//! * `record_id` (Utf8, non-null): the *original string identity* of the row.
//!   It is never used as an aggregation key — it exists so diagnostics and
//!   output can point back at the exact source row even when values collapse
//!   into one equivalence class.
//! * `value` (Utf8, nullable): the string the executors group/dedup on.
//!
//! Building goes through Arrow2 arrays (not plain `Vec`s) so the executor
//! genuinely reads columnar typed data.

use std::sync::Arc;

use arrow2::array::{Array, MutableUtf8Array, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};

use crate::error::{AppError, Result};

pub const COL_RECORD_ID: &str = "record_id";
pub const COL_VALUE: &str = "value";

/// One logical input row before it is columnarized. `None` value is SQL NULL.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub record_id: String,
    pub value: Option<String>,
}

impl Row {
    pub fn new(record_id: impl Into<String>, value: impl Into<String>) -> Self {
        Row {
            record_id: record_id.into(),
            value: Some(value.into()),
        }
    }

    pub fn null_value(record_id: impl Into<String>) -> Self {
        Row {
            record_id: record_id.into(),
            value: None,
        }
    }
}

/// The typed columnar input: a fixed-schema Arrow2 chunk.
#[derive(Clone, Debug)]
pub struct InputBatch {
    schema: Arc<Schema>,
    chunk: Chunk<Box<dyn Array>>,
}

impl InputBatch {
    /// Build from typed rows, materializing real Arrow2 arrays.
    pub fn from_rows(rows: &[Row]) -> Result<Self> {
        let mut ids = MutableUtf8Array::<i32>::with_capacity(rows.len());
        let mut vals = MutableUtf8Array::<i32>::with_capacity(rows.len());
        for r in rows {
            ids.push(Some(r.record_id.as_str()));
            vals.push(r.value.as_deref());
        }
        let ids: Utf8Array<i32> = ids.into();
        let values: Utf8Array<i32> = vals.into();

        let arrays: Vec<Box<dyn Array>> = vec![Box::new(ids), Box::new(values)];
        let chunk = Chunk::new(arrays);
        Ok(InputBatch {
            schema: Arc::new(schema()),
            chunk,
        })
    }

    pub fn len(&self) -> usize {
        self.chunk.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn schema(&self) -> &Schema {
        self.schema.as_ref()
    }

    /// Number of rows; exposed for diagnostics.
    pub fn row_count(&self) -> usize {
        self.len()
    }

    /// Read the two columns back out as typed, validity-aware accessors.
    pub fn columns(&self) -> Result<TypedColumns<'_>> {
        let cols = self.chunk.columns();
        let ids = cols[0]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .ok_or_else(|| AppError::Arrow("record_id is not Utf8Array<i32>".into()))?;
        let vals = cols[1]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .ok_or_else(|| AppError::Arrow("value is not Utf8Array<i32>".into()))?;
        Ok(TypedColumns { ids, vals })
    }
}

/// Borrowed typed view over the two string columns.
pub struct TypedColumns<'a> {
    ids: &'a Utf8Array<i32>,
    vals: &'a Utf8Array<i32>,
}

impl<'a> TypedColumns<'a> {
    pub fn len(&self) -> usize {
        self.vals.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Iterate `(record_id, Option<value>)` row by row, honoring Arrow
    /// validity rather than reading raw buffer bytes.
    pub fn iter(&self) -> impl Iterator<Item = (&'a str, Option<&'a str>)> + '_ {
        (0..self.len()).map(move |i| {
            let id = self.ids.value(i);
            let v = if self.vals.is_null(i) {
                None
            } else {
                Some(self.vals.value(i))
            };
            (id, v)
        })
    }
}

/// The canonical input schema.
pub fn schema() -> Schema {
    Schema::from(vec![
        Field::new(COL_RECORD_ID, DataType::Utf8, false),
        Field::new(COL_VALUE, DataType::Utf8, true),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_rows_through_arrow() -> Result<()> {
        let rows = vec![
            Row::new("r1", "Café"),
            Row::null_value("r2"),
            Row::new("r3", "cafeé"),
        ];
        let batch = InputBatch::from_rows(&rows)?;
        assert_eq!(batch.len(), 3);
        let cols = batch.columns()?;
        let got: Vec<(String, Option<String>)> = cols
            .iter()
            .map(|(id, v)| (id.to_string(), v.map(str::to_string)))
            .collect();
        assert_eq!(
            got,
            vec![
                ("r1".into(), Some("Café".into())),
                ("r2".into(), None),
                ("r3".into(), Some("cafeé".into())),
            ]
        );
        Ok(())
    }

    #[test]
    fn empty_batch_is_valid() -> Result<()> {
        let batch = InputBatch::from_rows(&[])?;
        assert!(batch.is_empty());
        assert_eq!(batch.row_count(), 0);
        Ok(())
    }
}
