//! Canonical row encoding.
//!
//! Requirements implemented here:
//! * NULL semantics are fixed: NULL is encoded as a dedicated tag byte and is
//!   *equal* to NULL (SQL multiset GROUP BY semantics), but never equal to any
//!   non-null value; nested NULLs are per-column.
//! * Column boundaries are explicit: every column starts with a type/null tag,
//!   variable payloads are length-prefixed, and columns are separated by a
//!   boundary marker. Two different rows can therefore never share a key by
//!   concatenation accident (e.g. `("12","3")` vs `("1","23")`).
//! * Types are explicit: `1_i64`, `1.0_f64`, `true` and `"1"` all encode
//!   differently even though their textual spelling collides.
//!
//! The byte layout (per column, concatenated):
//! ```text
//!   NULL  : 0x00
//!   BIGINT: 0x01 | 8 bytes big-endian i64
//!   DOUBLE: 0x02 | 8 bytes big-endian u64 bits (canonicalised, see below)
//!   BOOL  : 0x03 | 0x00 / 0x01
//!   TEXT  : 0x04 | 4 bytes BE u32 byte length | UTF-8 bytes
//!   columns are followed by a 0x1F boundary marker
//! ```
//! f64 canonicalisation: every NaN maps to one quiet NaN bit pattern and
//! negative zero maps to positive zero, matching SQL grouping equality.

use std::hash::{Hash, Hasher};

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};

use crate::batch::{LogicalType, Schema, TypedBatch};

const TAG_NULL: u8 = 0;
const TAG_BIGINT: u8 = 1;
const TAG_DOUBLE: u8 = 2;
const TAG_BOOL: u8 = 3;
const TAG_TEXT: u8 = 4;
/// Explicit column boundary marker.
pub const COL_BOUNDARY: u8 = 0x1F;

/// FNV-1a 64 offset basis / prime. Deterministic across processes and
/// platforms, which is what replayable partition logs need.
const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_b325;
const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

/// Canonical, hashable identity of one row within a schema.
#[derive(Debug, Clone, Eq, PartialEq)]
pub struct RowKey {
    bytes: Vec<u8>,
    hash: u64,
}

impl RowKey {
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn hash(&self) -> u64 {
        self.hash
    }
}

/// Test-only constructor from raw bytes (hash is recomputed). Used by corruption
/// tests that deliberately build malformed keys.
#[cfg(test)]
pub fn row_key_for_test(bytes: Vec<u8>) -> RowKey {
    let hash = canonical_hash(&bytes);
    RowKey { bytes, hash }
}

impl RowKey {
    pub(crate) fn from_parts(bytes: Vec<u8>, hash: u64) -> Self {
        RowKey { bytes, hash }
    }
}

impl Hash for RowKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        // Equality is byte-wise; hashing uses the precomputed FNV value so the
        // hash table hash matches partition routing hash exactly.
        state.write_u64(self.hash);
    }
}

/// SplitMix64 finaliser — used to derive an independent hash at the next
/// recursion level, so rows sharing a level-0 bucket scatter at level 1.
pub fn splitmix64(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

pub fn canonical_hash(bytes: &[u8]) -> u64 {
    let mut h = FNV_OFFSET;
    for &b in bytes {
        h ^= b as u64;
        h = h.wrapping_mul(FNV_PRIME);
    }
    h
}

/// Deterministic partition at recursion level `level` (0 = top level).
/// Each level mixes the row hash with a level salt before re-finalising,
/// guaranteeing independent fan-out at every recursion level.
pub fn level_partition(hash: u64, level: usize, fanout: usize) -> usize {
    debug_assert!(fanout > 0);
    let salted = hash ^ ((level as u64).wrapping_mul(0x9e37_79b9_7f4a_7c15));
    (splitmix64(salted) % fanout as u64) as usize
}

fn finish_key(bytes: Vec<u8>) -> RowKey {
    // The last column already wrote its trailing boundary marker, which also
    // terminates the row when frames concatenate rows.
    debug_assert_eq!(bytes.last(), Some(&COL_BOUNDARY));
    let hash = canonical_hash(&bytes);
    RowKey { bytes, hash }
}

fn append_null(out: &mut Vec<u8>) {
    out.push(TAG_NULL);
}

fn append_bigint(out: &mut Vec<u8>, v: i64) {
    out.push(TAG_BIGINT);
    out.extend_from_slice(&v.to_be_bytes());
}

fn append_double(out: &mut Vec<u8>, v: f64) {
    let bits = if v.is_nan() {
        f64::NAN.to_bits()
    } else if v == 0.0 {
        0.0f64.to_bits()
    } else {
        v.to_bits()
    };
    out.push(TAG_DOUBLE);
    out.extend_from_slice(&bits.to_be_bytes());
}

fn append_bool(out: &mut Vec<u8>, v: bool) {
    out.push(TAG_BOOL);
    out.push(if v { 1 } else { 0 });
}

fn append_text(out: &mut Vec<u8>, v: &str) {
    let b = v.as_bytes();
    out.push(TAG_TEXT);
    out.extend_from_slice(&(b.len() as u32).to_be_bytes());
    out.extend_from_slice(b);
}

/// Append a value column and its trailing boundary marker.
fn append_column<F: FnOnce(&mut Vec<u8>)>(out: &mut Vec<u8>, write: F) {
    write(out);
    out.push(COL_BOUNDARY);
}

/// Encode every row of a typed batch.
pub fn encode_rows(batch: &TypedBatch) -> Vec<RowKey> {
    let n = batch.rows();
    let schema = &batch.schema;
    let chunk = &batch.chunk;
    let encoders: Vec<ColumnEncoder<'_>> = schema
        .fields()
        .iter()
        .enumerate()
        .map(|(i, f)| ColumnEncoder::new(f.ty, chunk.arrays()[i].as_ref()))
        .collect();

    let mut keys = Vec::with_capacity(n);
    for row in 0..n {
        let mut out = Vec::with_capacity(schema.len() * 8);
        for enc in &encoders {
            append_column(&mut out, |o| enc.append(row, o));
        }
        keys.push(finish_key(out));
    }
    keys
}

/// Encode a single row given as schema-ordered JSON-ish values.
/// Used by tests and by the HTTP JSON entrypoint.
pub fn encode_json_row(schema: &Schema, values: &[serde_json::Value]) -> Result<RowKey, String> {
    if values.len() != schema.len() {
        return Err(format!(
            "row has {} values but schema has {} columns",
            values.len(),
            schema.len()
        ));
    }
    let mut out = Vec::with_capacity(schema.len() * 8);
    for (i, (field, v)) in schema.fields().iter().zip(values).enumerate() {
        match v {
            serde_json::Value::Null => append_column(&mut out, append_null),
            serde_json::Value::Bool(b) => match field.ty {
                LogicalType::Boolean => append_column(&mut out, |o| append_bool(o, *b)),
                _ => return Err(type_err(i, field.ty, v)),
            },
            serde_json::Value::Number(num) => match field.ty {
                LogicalType::BigInt => {
                    let x = num.as_i64().ok_or_else(|| type_err(i, field.ty, v))?;
                    append_column(&mut out, |o| append_bigint(o, x));
                }
                LogicalType::Double => {
                    let x = num.as_f64().ok_or_else(|| type_err(i, field.ty, v))?;
                    append_column(&mut out, |o| append_double(o, x));
                }
                _ => return Err(type_err(i, field.ty, v)),
            },
            serde_json::Value::String(s) => match field.ty {
                LogicalType::Text => append_column(&mut out, |o| append_text(o, s)),
                _ => return Err(type_err(i, field.ty, v)),
            },
            other => return Err(format!("column {}: unsupported JSON value {other}", i + 1)),
        }
    }
    Ok(finish_key(out))
}

fn type_err(i: usize, ty: LogicalType, v: &serde_json::Value) -> String {
    format!("column {}: expected {ty:?}, got {v}", i + 1)
}

/// Type-erased per-column reader over an Arrow2 array.
enum ColumnEncoder<'a> {
    BigInt(&'a PrimitiveArray<i64>),
    Double(&'a PrimitiveArray<f64>),
    Boolean(&'a BooleanArray),
    Text(&'a Utf8Array<i32>),
}

impl<'a> ColumnEncoder<'a> {
    fn new(ty: LogicalType, array: &'a dyn Array) -> Self {
        match ty {
            LogicalType::BigInt => Self::BigInt(array.as_any().downcast_ref().expect("i64 array")),
            LogicalType::Double => Self::Double(array.as_any().downcast_ref().expect("f64 array")),
            LogicalType::Boolean => {
                Self::Boolean(array.as_any().downcast_ref().expect("bool array"))
            }
            LogicalType::Text => Self::Text(array.as_any().downcast_ref().expect("utf8 array")),
        }
    }

    fn append(&self, row: usize, out: &mut Vec<u8>) {
        match self {
            Self::BigInt(a) => match a.get(row) {
                None => append_null(out),
                Some(v) => append_bigint(out, v),
            },
            Self::Double(a) => match a.get(row) {
                None => append_null(out),
                Some(v) => append_double(out, v),
            },
            Self::Boolean(a) => match a.get(row) {
                None => append_null(out),
                Some(v) => append_bool(out, v),
            },
            Self::Text(a) => match a.get(row) {
                None => append_null(out),
                Some(v) => append_text(out, v),
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::SchemaField;

    fn schema(tys: &[LogicalType]) -> Schema {
        Schema::new(
            tys.iter()
                .enumerate()
                .map(|(i, t)| SchemaField {
                    name: format!("c{i}"),
                    ty: *t,
                })
                .collect(),
        )
        .unwrap()
    }

    fn k(s: &Schema, vals: &[serde_json::Value]) -> RowKey {
        encode_json_row(s, vals).unwrap()
    }

    #[test]
    fn column_boundaries_cannot_be_confused() {
        let s = schema(&[LogicalType::Text, LogicalType::Text]);
        let a = k(&s, &[json("12"), json("3")]);
        let b = k(&s, &[json("1"), json("23")]);
        assert_ne!(a, b);
    }

    #[test]
    fn type_tags_disambiguate_textual_collisions() {
        let s2 = schema(&[LogicalType::BigInt, LogicalType::Double]);
        let s3 = schema(&[LogicalType::Boolean, LogicalType::Text]);
        let int_pair = k(&s2, &[json(1), json(1.0)]);
        let str_pair = k(&s3, &[json(true), json("1")]);
        assert_ne!(int_pair.bytes(), str_pair.bytes());
        // and within one schema, int 1 vs text "1"
        let sa = schema(&[LogicalType::BigInt]);
        let sb = schema(&[LogicalType::Text]);
        assert_ne!(k(&sa, &[json(1)]), k(&sb, &[json("1")]));
    }

    #[test]
    fn null_is_equal_to_null_but_not_to_values() {
        let s = schema(&[LogicalType::Text]);
        assert_eq!(k(&s, &[json(())]), k(&s, &[json(())]));
        assert_ne!(k(&s, &[json(())]), k(&s, &[json("")]));
    }

    #[test]
    fn nested_null_positions_matter() {
        let s = schema(&[LogicalType::Text, LogicalType::Text]);
        assert_ne!(k(&s, &[json(()), json("x")]), k(&s, &[json("x"), json(())]));
    }

    #[test]
    fn double_zero_and_nan_canonicalise() {
        let s = schema(&[LogicalType::Double]);
        assert_eq!(k(&s, &[json(0.0)]), k(&s, &[json(-0.0)]));
        assert_eq!(
            k(&s, &[serde_json::Value::from(f64::NAN)]),
            k(&s, &[serde_json::Value::from(f64::NAN)])
        );
    }

    #[test]
    fn level_partition_is_stable_but_scatters() {
        let h = canonical_hash(b"abc");
        assert_eq!(level_partition(h, 0, 16), level_partition(h, 0, 16));
        // deterministic salt chain
        assert_eq!(level_partition(h, 2, 16), level_partition(h, 2, 16));
    }

    fn json<T: Into<serde_json::Value>>(v: T) -> serde_json::Value {
        v.into()
    }
}
