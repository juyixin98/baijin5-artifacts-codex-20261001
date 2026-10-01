//! Decode canonical [`RowKey`] bytes back into a typed Arrow2 chunk.
//!
//! Decoding is the inverse contract of `encode`: it validates every tag,
//! length and boundary byte, so a corrupted spill frame produces a
//! `state_conflict/spill_corrupted` error instead of wrong data.

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;

use crate::batch::encode::{COL_BOUNDARY, RowKey};
use crate::batch::{LogicalType, Schema, TypedBatch};
use crate::error::{SetOpsError, StateCode};

fn corrupt(msg: impl Into<String>) -> SetOpsError {
    SetOpsError::state(StateCode::SpillCorrupted, msg)
}

struct Cursor<'a> {
    buf: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    fn new(buf: &'a [u8]) -> Self {
        Self { buf, pos: 0 }
    }
    fn u8(&mut self) -> Result<u8, SetOpsError> {
        let b = *self
            .buf
            .get(self.pos)
            .ok_or_else(|| corrupt("truncated value"))?;
        self.pos += 1;
        Ok(b)
    }
    fn take(&mut self, n: usize) -> Result<&'a [u8], SetOpsError> {
        let end = self
            .pos
            .checked_add(n)
            .ok_or_else(|| corrupt("length overflow"))?;
        let s = self
            .buf
            .get(self.pos..end)
            .ok_or_else(|| corrupt("truncated payload"))?;
        self.pos = end;
        Ok(s)
    }
    fn boundary(&mut self) -> Result<(), SetOpsError> {
        match self.u8()? {
            COL_BOUNDARY => Ok(()),
            other => Err(corrupt(format!(
                "expected column boundary 0x1F, got 0x{other:02x}"
            ))),
        }
    }
    fn done(&self) -> bool {
        self.pos == self.buf.len()
    }
}

/// Decode one key against an expected schema.
pub fn decode_key(schema: &Schema, key: &RowKey) -> Result<Vec<ScalarValue>, SetOpsError> {
    let mut cur = Cursor::new(key.bytes());
    let mut out = Vec::with_capacity(schema.len());
    for field in schema.fields() {
        let tag = cur.u8()?;
        let v = match (tag, field.ty) {
            (0, _) => ScalarValue::Null,
            (1, LogicalType::BigInt) => {
                let b: [u8; 8] = cur.take(8)?.try_into().unwrap();
                ScalarValue::BigInt(i64::from_be_bytes(b))
            }
            (2, LogicalType::Double) => {
                let b: [u8; 8] = cur.take(8)?.try_into().unwrap();
                ScalarValue::Double(f64::from_be_bytes(b))
            }
            (3, LogicalType::Boolean) => ScalarValue::Boolean(match cur.u8()? {
                0 => false,
                1 => true,
                other => return Err(corrupt(format!("invalid bool byte {other}"))),
            }),
            (4, LogicalType::Text) => {
                let lb: [u8; 4] = cur.take(4)?.try_into().unwrap();
                let len = u32::from_be_bytes(lb) as usize;
                let bytes = cur.take(len)?;
                let s = std::str::from_utf8(bytes)
                    .map_err(|_| corrupt("text payload is not valid UTF-8"))?;
                ScalarValue::Text(s.to_string())
            }
            (tag, ty) => {
                return Err(corrupt(format!(
                    "type tag {tag} does not match declared column type {ty:?}"
                )));
            }
        };
        cur.boundary()?;
        out.push(v);
    }
    if !cur.done() {
        return Err(corrupt("trailing bytes after last column"));
    }
    Ok(out)
}

#[derive(Debug, Clone)]
pub enum ScalarValue {
    Null,
    BigInt(i64),
    Double(f64),
    Boolean(bool),
    Text(String),
}

/// Build a [`TypedBatch`] from keys, repeating each key according to its output
/// multiplicity. Keys must all be encodable under `schema`.
pub fn decode_keys(
    schema: &Schema,
    keys: impl IntoIterator<Item = (RowKey, u64)>,
) -> Result<TypedBatch, SetOpsError> {
    let pairs: Vec<(RowKey, u64)> = keys.into_iter().filter(|(_, mult)| *mult > 0).collect();
    let total: usize = pairs
        .iter()
        .map(|(_, m)| usize::try_from(*m).map_err(|_| corrupt("multiplicity > usize")))
        .collect::<Result<Vec<_>, _>>()?
        .iter()
        .sum();

    // Decoupled validity/value buffers per column.
    let mut builders: Vec<ColumnBuilder> = schema
        .fields()
        .iter()
        .map(|f| ColumnBuilder::with_capacity(f.ty, total))
        .collect();

    for (key, mult) in pairs {
        let values = decode_key(schema, &key)?;
        for _ in 0..mult {
            for (c, v) in values.iter().enumerate() {
                builders[c].push(v);
            }
        }
    }

    let arrays: Vec<Box<dyn Array>> = builders.into_iter().map(|b| b.finish()).collect();
    let chunk = Chunk::try_new(arrays).map_err(|e| corrupt(format!("chunk build failed: {e}")))?;
    Ok(TypedBatch::new(schema.clone(), chunk))
}

enum ColumnBuilder {
    BigInt(Vec<Option<i64>>),
    Double(Vec<Option<f64>>),
    Boolean(Vec<Option<bool>>),
    Text(Vec<Option<String>>),
}

impl ColumnBuilder {
    fn with_capacity(ty: LogicalType, cap: usize) -> Self {
        match ty {
            LogicalType::BigInt => Self::BigInt(Vec::with_capacity(cap)),
            LogicalType::Double => Self::Double(Vec::with_capacity(cap)),
            LogicalType::Boolean => Self::Boolean(Vec::with_capacity(cap)),
            LogicalType::Text => Self::Text(Vec::with_capacity(cap)),
        }
    }
    fn push(&mut self, v: &ScalarValue) {
        match (self, v) {
            (Self::BigInt(x), ScalarValue::BigInt(i)) => x.push(Some(*i)),
            (Self::Double(x), ScalarValue::Double(d)) => x.push(Some(*d)),
            (Self::Boolean(x), ScalarValue::Boolean(b)) => x.push(Some(*b)),
            (Self::Text(x), ScalarValue::Text(s)) => x.push(Some(s.clone())),
            (target, ScalarValue::Null) => target.push_none(),
            (s, v) => panic!("builder/value mismatch {s:?} vs {v:?}"),
        }
    }
    fn push_none(&mut self) {
        match self {
            Self::BigInt(x) => x.push(None),
            Self::Double(x) => x.push(None),
            Self::Boolean(x) => x.push(None),
            Self::Text(x) => x.push(None),
        }
    }
    fn finish(self) -> Box<dyn Array> {
        match self {
            Self::BigInt(x) => Box::new(PrimitiveArray::<i64>::from(x)),
            Self::Double(x) => Box::new(PrimitiveArray::<f64>::from(x)),
            Self::Boolean(x) => Box::new(BooleanArray::from(x)),
            Self::Text(x) => Box::new(Utf8Array::<i32>::from(x)),
        }
    }
}

impl std::fmt::Debug for ColumnBuilder {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "ColumnBuilder")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::encode::encode_json_row;

    #[test]
    fn roundtrip_all_types_with_nulls() {
        let schema = Schema::parse_header("a:bigint,b:double,c:bool,d:text").unwrap();
        let row = serde_json::json!([-7, 2.5, true, "héllo\x1f"]);
        let key = encode_json_row(&schema, row.as_array().unwrap()).unwrap();
        let vals = decode_key(&schema, &key).unwrap();
        assert!(matches!(vals[0], ScalarValue::BigInt(-7)));
        assert!(matches!(vals[2], ScalarValue::Boolean(true)));
        match &vals[3] {
            ScalarValue::Text(s) => assert_eq!(s, "héllo\x1f"),
            other => panic!("{other:?}"),
        }
        let null_key = encode_json_row(
            &schema,
            &[
                serde_json::Value::Null,
                serde_json::Value::Null,
                serde_json::Value::Null,
                serde_json::Value::Null,
            ],
        )
        .unwrap();
        let batch = decode_keys(&schema, [(null_key, 2)]).unwrap();
        assert_eq!(batch.rows(), 2);
        for c in 0..4 {
            assert_eq!(
                batch.chunk.arrays()[c]
                    .validity()
                    .map(|v| v.unset_bits())
                    .unwrap_or(0),
                2
            );
        }
    }

    #[test]
    fn rejects_corrupt_frames() {
        use crate::batch::encode::row_key_for_test;
        let schema = Schema::parse_header("a:bigint").unwrap();
        let key = encode_json_row(&schema, &[serde_json::json!(1)]).unwrap();
        let mut bytes = key.bytes().to_vec();
        // flip the type tag to text (4)
        bytes[0] = 4;
        let bad = row_key_for_test(bytes);
        assert!(decode_key(&schema, &bad).is_err());

        let mut bytes = key.bytes().to_vec();
        bytes.pop();
        assert!(decode_key(&schema, &row_key_for_test(bytes)).is_err());
    }
}
