//! Typed columnar batches backed by arrow2.
//!
//! A [`StringBatch`] is the real data carrier through the operators: an arrow2
//! [`Utf8Array`] for the ORIGINAL string identities plus the logical column name.
//! Keeping the raw `Vec<String>` alongside the array lets executors inspect identity
//! cheaply while the array proves the data lives in a real columnar representation.

use arrow2::array::{Array, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};

/// A typed batch of string identities in one named column.
#[derive(Debug, Clone)]
pub struct StringBatch {
    column: String,
    values: Vec<String>,
    array: Utf8Array<i32>,
}

impl StringBatch {
    /// Build a batch, validating at the system boundary.
    pub fn try_new(column: impl Into<String>, values: Vec<String>) -> Result<Self, BatchError> {
        let column = column.into();
        if column.is_empty() {
            return Err(BatchError::EmptyColumnName);
        }
        let array = Utf8Array::<i32>::from_slice(&values);
        Ok(Self {
            column,
            values,
            array,
        })
    }

    pub fn column(&self) -> &str {
        &self.column
    }

    pub fn len(&self) -> usize {
        self.values.len()
    }

    pub fn is_empty(&self) -> bool {
        self.values.is_empty()
    }

    /// Original string identities (NOT aggregation keys).
    pub fn values(&self) -> &[String] {
        &self.values
    }

    pub fn iter(&self) -> impl Iterator<Item = &str> {
        self.values.iter().map(String::as_str)
    }

    /// The arrow2 column array.
    pub fn array(&self) -> &Utf8Array<i32> {
        &self.array
    }

    /// Materialize the batch as an arrow2 [`Chunk`] (one column).
    pub fn to_chunk(&self) -> Chunk<Box<dyn Array>> {
        Chunk::new(vec![self.array.clone().boxed()])
    }

    /// The arrow2 schema for this batch.
    pub fn schema(&self) -> Schema {
        Schema::from(vec![Field::new(self.column.clone(), DataType::Utf8, false)])
    }
}

/// Errors raised when constructing/parsing a batch at the boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BatchError {
    EmptyColumnName,
    JsonShape(String),
    JsonParse(String),
}

impl std::fmt::Display for BatchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BatchError::EmptyColumnName => f.write_str("column name must not be empty"),
            BatchError::JsonShape(m) => write!(f, "invalid batch shape: {m}"),
            BatchError::JsonParse(m) => write!(f, "invalid JSON batch: {m}"),
        }
    }
}
impl std::error::Error for BatchError {}

/// Parse rows of the form `[{"<column>": "..."}, ...]` into a typed batch.
pub fn batch_from_json_rows(column: &str, raw: &[u8]) -> Result<StringBatch, BatchError> {
    // arrow2's JSON reader needs NDJSON; build it ourselves through serde_json for a
    // precise, boundary-validating parse, then verify the array round-trips via arrow2.
    let rows: Vec<serde_json::Value> =
        serde_json::from_slice(raw).map_err(|e| BatchError::JsonParse(e.to_string()))?;
    if !rows.iter().all(|r| r.is_object()) {
        return Err(BatchError::JsonShape(
            "expected an array of JSON objects".into(),
        ));
    }
    let mut values = Vec::with_capacity(rows.len());
    for (i, row) in rows.into_iter().enumerate() {
        let v = row
            .get(column)
            .ok_or_else(|| BatchError::JsonShape(format!("row {i} missing column `{column}`")))?;
        match v {
            serde_json::Value::String(s) => values.push(s.clone()),
            serde_json::Value::Null => {
                return Err(BatchError::JsonShape(format!(
                    "row {i} column `{column}` is null; strings only"
                )))
            }
            other => {
                return Err(BatchError::JsonShape(format!(
                    "row {i} column `{column}` is {other}, expected string"
                )))
            }
        }
    }
    let batch = StringBatch::try_new(column, values)?;
    debug_assert_eq!(batch.array().len(), batch.len());
    Ok(batch)
}

/// Build a batch from in-memory rows (used by tests and by callers that already parsed
/// JSON at the transport boundary). Kept here so batch construction has one home.
pub fn batch_from_strings(column: &str, values: Vec<String>) -> Result<StringBatch, BatchError> {
    StringBatch::try_new(column, values)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_and_shares_array_length() {
        // "Café" written with an explicit \u escape so the byte string stays ASCII.
        let raw = "[{\"name\":\"Caf\u{e9}\"},{\"name\":\"cafe\"}]";
        let b = batch_from_json_rows("name", raw.as_bytes()).unwrap();
        assert_eq!(b.len(), 2);
        assert_eq!(b.array().value(0), "Caf\u{e9}");
        assert_eq!(b.column(), "name");
        assert_eq!(b.schema().fields.len(), 1);
        assert_eq!(b.to_chunk().columns()[0].len(), 2);
    }

    #[test]
    fn rejects_bad_shapes() {
        assert!(matches!(
            batch_from_json_rows("name", br#"[{"other":"x"}]"#),
            Err(BatchError::JsonShape(_))
        ));
        assert!(matches!(
            batch_from_json_rows("name", br#"[{"name":1}]"#),
            Err(BatchError::JsonShape(_))
        ));
        assert!(matches!(
            batch_from_json_rows("name", br#"[{"name":null}]"#),
            Err(BatchError::JsonShape(_))
        ));
        assert!(matches!(
            StringBatch::try_new("", vec!["x".to_string()]),
            Err(BatchError::EmptyColumnName)
        ));
    }
}
