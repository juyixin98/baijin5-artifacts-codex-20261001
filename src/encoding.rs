//! Injective row encoding.
//!
//! The partition hash is *only* a routing hint: hash buckets can and do
//! collide, so every equality decision is made on the exact encoded bytes
//! produced here. The encoding must therefore be injective: two rows are equal
//! under set semantics iff their encodings are byte-identical.
//!
//! Guarantees built into the format:
//!
//! - **Explicit column boundary** (`COL_MARK`) between every column.
//! - **A type tag on every node**, so e.g. `1i64`, `"1"`, `true` and a
//!   one-element list cannot collide.
//! - **Length-prefixed strings/counts**: a column marker or tag byte appearing
//!   *inside* a string payload is consumed as payload (never as a boundary),
//!   because exactly `len` bytes are read. Embedded NULs, FE/FF bytes, and
//!   arbitrary Unicode are therefore safe.
//! - **NULL is a first-class tag**, distinct from the empty string and the
//!   empty list; SQL set equality (NULL = NULL) falls out of byte equality.
//!
//! The format is fully decodable back into [`Value`]; the round-trip test in
//! this module is what certifies injectivity on the adversarial fixtures
//! (boundary-byte strings, nested NULLs, empty vs NULL containers).

use std::hash::{Hash, Hasher};

use crate::error::{Result, SetOpError};
use crate::value::{DataType, Value};

const TAG_NULL: u8 = 0x00;
const TAG_BOOL_T: u8 = 0x01;
const TAG_BOOL_F: u8 = 0x02;
const TAG_INT64: u8 = 0x03;
const TAG_UTF8: u8 = 0x04;
const TAG_LIST: u8 = 0x05;
const TAG_STRUCT: u8 = 0x06;
const COL_MARK: u8 = 0xFE;
const ROW_END: u8 = 0xFF;

/// Encode one row (ordered column values) into a canonical byte key.
pub fn encode_row(row: &[Value]) -> Vec<u8> {
    let mut out = Vec::with_capacity(row.len() * 8);
    for v in row {
        out.push(COL_MARK);
        encode_value(v, &mut out);
    }
    out.push(ROW_END);
    out
}

fn encode_value(v: &Value, out: &mut Vec<u8>) {
    match v {
        Value::Null => out.push(TAG_NULL),
        Value::Bool(true) => out.push(TAG_BOOL_T),
        Value::Bool(false) => out.push(TAG_BOOL_F),
        Value::Int64(i) => {
            out.push(TAG_INT64);
            out.extend_from_slice(&i.to_be_bytes());
        }
        Value::Utf8(s) => {
            out.push(TAG_UTF8);
            write_u64(s.len() as u64, out);
            out.extend_from_slice(s.as_bytes());
        }
        Value::List(items) => {
            out.push(TAG_LIST);
            write_u64(items.len() as u64, out);
            for item in items {
                encode_value(item, out);
            }
        }
        Value::Struct(fields) => {
            out.push(TAG_STRUCT);
            write_u64(fields.len() as u64, out);
            for (_, fv) in fields {
                encode_value(fv, out);
            }
        }
    }
}

fn write_u64(n: u64, out: &mut Vec<u8>) {
    out.extend_from_slice(&n.to_be_bytes());
}

fn read_u64(buf: &[u8], pos: &mut usize) -> Result<u64> {
    if *pos + 8 > buf.len() {
        return Err(SetOpError::compute("bad_encoding", "truncated u64 length"));
    }
    let mut be = [0u8; 8];
    be.copy_from_slice(&buf[*pos..*pos + 8]);
    *pos += 8;
    Ok(u64::from_be_bytes(be))
}

/// Decode a row previously produced by [`encode_row`], expecting `ncols`.
pub fn decode_row(buf: &[u8], ncols: usize) -> Result<Vec<Value>> {
    let mut pos = 0;
    let mut cols = Vec::with_capacity(ncols);
    for _ in 0..ncols {
        if pos >= buf.len() || buf[pos] != COL_MARK {
            return Err(SetOpError::compute("bad_encoding", "missing column marker"));
        }
        pos += 1;
        cols.push(decode_value(buf, &mut pos)?);
    }
    if pos != buf.len() - 1 || buf[pos] != ROW_END {
        return Err(SetOpError::compute(
            "bad_encoding",
            "missing row end / trailing bytes",
        ));
    }
    Ok(cols)
}

fn decode_value(buf: &[u8], pos: &mut usize) -> Result<Value> {
    if *pos >= buf.len() {
        return Err(SetOpError::compute("bad_encoding", "truncated value tag"));
    }
    let tag = buf[*pos];
    *pos += 1;
    match tag {
        TAG_NULL => Ok(Value::Null),
        TAG_BOOL_T => Ok(Value::Bool(true)),
        TAG_BOOL_F => Ok(Value::Bool(false)),
        TAG_INT64 => {
            if *pos + 8 > buf.len() {
                return Err(SetOpError::compute("bad_encoding", "truncated int64"));
            }
            let mut be = [0u8; 8];
            be.copy_from_slice(&buf[*pos..*pos + 8]);
            *pos += 8;
            Ok(Value::Int64(i64::from_be_bytes(be)))
        }
        TAG_UTF8 => {
            let len = read_u64(buf, pos)? as usize;
            if *pos + len > buf.len() {
                return Err(SetOpError::compute(
                    "bad_encoding",
                    "truncated utf8 payload",
                ));
            }
            let s = std::str::from_utf8(&buf[*pos..*pos + len])
                .map_err(|_| SetOpError::compute("bad_encoding", "invalid utf8 payload"))?
                .to_string();
            *pos += len;
            Ok(Value::Utf8(s))
        }
        TAG_LIST => {
            let n = read_u64(buf, pos)? as usize;
            let mut items = Vec::with_capacity(n.min(1 << 20));
            for _ in 0..n {
                items.push(decode_value(buf, pos)?);
            }
            Ok(Value::List(items))
        }
        TAG_STRUCT => {
            // Field names are recovered positionally by callers that know the
            // schema; the encoding itself is nameless (schema is fixed). Decode
            // to a list-like structure carrying empty names is avoided: we
            // return a Struct with empty names, which is only used in
            // schema-free round-trip tests.
            let n = read_u64(buf, pos)? as usize;
            let mut fields = Vec::with_capacity(n.min(1 << 20));
            for _ in 0..n {
                fields.push((String::new(), decode_value(buf, pos)?));
            }
            Ok(Value::Struct(fields))
        }
        other => Err(SetOpError::compute(
            "bad_encoding",
            format!("unknown type tag: {other:#x}"),
        )),
    }
}

/// Decode one value against an *expected* logical type.
///
/// Beyond plain decoding this validates that the encoded type tag matches the
/// schema type (so a corrupted/heterogeneous key cannot be misread), checks
/// integer and length bounds, and restores struct field names from the schema
/// (names are not stored in the key; they are filled in by position).
pub fn decode_typed(buf: &[u8], pos: &mut usize, expected: &DataType) -> Result<Value> {
    if *pos >= buf.len() {
        return Err(SetOpError::compute("bad_encoding", "truncated value tag"));
    }
    let tag = buf[*pos];
    *pos += 1;
    // NULL is legal for every type.
    if tag == TAG_NULL {
        return Ok(Value::Null);
    }
    match (tag, expected) {
        (TAG_BOOL_T, DataType::Bool) => Ok(Value::Bool(true)),
        (TAG_BOOL_F, DataType::Bool) => Ok(Value::Bool(false)),
        (TAG_INT64, DataType::Int64) => {
            if *pos + 8 > buf.len() {
                return Err(SetOpError::compute("bad_encoding", "truncated int64"));
            }
            let mut be = [0u8; 8];
            be.copy_from_slice(&buf[*pos..*pos + 8]);
            *pos += 8;
            Ok(Value::Int64(i64::from_be_bytes(be)))
        }
        (TAG_UTF8, DataType::Utf8) => {
            let len = read_u64(buf, pos)? as usize;
            if *pos + len > buf.len() {
                return Err(SetOpError::compute(
                    "bad_encoding",
                    "truncated utf8 payload",
                ));
            }
            let s = std::str::from_utf8(&buf[*pos..*pos + len])
                .map_err(|_| SetOpError::compute("bad_encoding", "invalid utf8 payload"))?
                .to_string();
            *pos += len;
            Ok(Value::Utf8(s))
        }
        (TAG_LIST, DataType::List(inner)) => {
            let n = read_u64(buf, pos)? as usize;
            let mut items = Vec::with_capacity(n.min(1 << 20));
            for _ in 0..n {
                items.push(decode_typed(buf, pos, &inner.data_type)?);
            }
            Ok(Value::List(items))
        }
        (TAG_STRUCT, DataType::Struct { fields: field_defs }) => {
            let n = read_u64(buf, pos)? as usize;
            if n != field_defs.len() {
                return Err(SetOpError::compute(
                    "bad_encoding",
                    format!(
                        "struct field count {n} does not match schema {}",
                        field_defs.len()
                    ),
                ));
            }
            let mut fields = Vec::with_capacity(n);
            for fd in field_defs {
                fields.push((fd.name.clone(), decode_typed(buf, pos, &fd.data_type)?));
            }
            Ok(Value::Struct(fields))
        }
        (other_tag, exp) => Err(SetOpError::compute(
            "bad_encoding",
            format!(
                "type tag {other_tag:#x} does not match expected type {}",
                exp.name()
            ),
        )),
    }
}

/// Decode a full row key against a schema, verifying every column boundary and
/// the trailing row marker.
pub fn decode_row_typed(buf: &[u8], schema: &crate::value::Schema) -> Result<Vec<Value>> {
    let mut pos = 0;
    let mut cols = Vec::with_capacity(schema.fields.len());
    for f in &schema.fields {
        if pos >= buf.len() || buf[pos] != COL_MARK {
            return Err(SetOpError::compute("bad_encoding", "missing column marker"));
        }
        pos += 1;
        cols.push(decode_typed(buf, &mut pos, &f.data_type)?);
    }
    if pos + 1 != buf.len() || buf[pos] != ROW_END {
        return Err(SetOpError::compute(
            "bad_encoding",
            "missing row end / trailing bytes",
        ));
    }
    Ok(cols)
}

/// Deterministic 64-bit hash of a row key at a given recursion level.
///
/// `level` salts the hash so that when a spilled partition is re-split the
/// sub-partitions differ from the parent split. The hash is never used for
/// equality: distinct rows sharing a bucket are compared by exact key.
pub fn hash_key(key: &[u8], level: u32) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    level.hash(&mut h);
    Hasher::write(&mut h, key);
    h.finish()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::value::value_to_json;

    fn row(values: Vec<Value>) -> Vec<u8> {
        encode_row(&values)
    }

    #[test]
    fn null_differs_from_empty_scalars_and_containers() {
        let null = row(vec![Value::Null]);
        let empty_str = row(vec![Value::Utf8(String::new())]);
        let empty_list = row(vec![Value::List(vec![])]);
        let falsy = row(vec![Value::Bool(false)]);
        let zero = row(vec![Value::Int64(0)]);
        let keys = [null, empty_str, empty_list, falsy, zero];
        for i in 0..keys.len() {
            for j in (i + 1)..keys.len() {
                assert_ne!(keys[i], keys[j], "keys {i} and {j} collided");
            }
        }
    }

    #[test]
    fn tag_bytes_inside_strings_do_not_shift_columns() {
        // 0xFE/0xFF can never occur inside valid UTF-8 (they are always
        // continuation/lead bytes), so the only bytes that can be *confused*
        // with structural markers are the value tag bytes 0x00..=0x06, which
        // are all valid single-byte UTF-8 control characters. Embed every tag
        // byte, repeatedly, plus a decoy shaped like a length-prefixed int, in
        // BOTH columns. Length prefixing must keep the parser aligned.
        let mut poison = String::new();
        for b in 0u8..=6u8 {
            poison.push(char::from(b));
        }
        // Decoy: TAG_UTF8-looking byte, an 8-byte big-endian length, payload.
        poison.push(char::from(TAG_UTF8));
        for byte in 42u64.to_be_bytes() {
            poison.push(char::from(byte));
        }
        // Embed a NUL tag that, without length prefixing, would read as NULL.
        poison.push(char::from(TAG_NULL));

        let rows: Vec<Vec<Value>> = vec![
            vec![Value::Utf8(poison.clone()), Value::Utf8(String::new())],
            vec![Value::Utf8(String::new()), Value::Utf8(poison.clone())],
            vec![Value::Utf8(poison.clone()), Value::Utf8(poison.clone())],
        ];
        for r in &rows {
            let key = encode_row(r);
            let back = decode_row(&key, 2).unwrap();
            assert_eq!(&back, r);
        }
        // The three placements (which column holds the poison) stay distinct.
        let keys: Vec<Vec<u8>> = rows.iter().map(|r| encode_row(r)).collect();
        assert_ne!(keys[0], keys[1]);
        assert_ne!(keys[1], keys[2]);
        assert_ne!(keys[0], keys[2]);
    }

    #[test]
    fn type_tags_prevent_cross_type_collision() {
        // 1 vs "1" vs true vs [1] vs struct(1)
        let variants: Vec<Vec<Value>> = vec![
            vec![Value::Int64(1)],
            vec![Value::Utf8("1".into())],
            vec![Value::Bool(true)],
            vec![Value::List(vec![Value::Int64(1)])],
            vec![Value::Struct(vec![("f".into(), Value::Int64(1))])],
        ];
        let keys: Vec<Vec<u8>> = variants.iter().map(|r| row(r.clone())).collect();
        for i in 0..keys.len() {
            for j in (i + 1)..keys.len() {
                assert_ne!(keys[i], keys[j]);
            }
            // round trip (struct name is schema-filled in real use; compare JSON
            // of non-struct cases).
            if !matches!(variants[i][0], Value::Struct(_)) {
                assert_eq!(decode_row(&keys[i], 1).unwrap(), variants[i]);
            }
        }
    }

    #[test]
    fn nested_nulls_roundtrip() {
        use serde_json::json;
        let ty = crate::value::DataType::List(Box::new(crate::value::Field {
            name: "e".into(),
            data_type: crate::value::DataType::List(Box::new(crate::value::Field {
                name: "e".into(),
                data_type: crate::value::DataType::Utf8,
                nullable: true,
            })),
            nullable: true,
        }));
        let v = crate::value::json_to_value(&ty, &json!([["a", null], [], [null], [null, ""]]))
            .unwrap();
        let key = row(vec![v.clone()]);
        let back = decode_row(&key, 1).unwrap()[0].clone();
        // Struct names aside, compare via JSON where list/null shape is exact.
        assert_eq!(value_to_json(&v), value_to_json(&back));
    }

    #[test]
    fn hashing_routes_but_equality_uses_exact_key() {
        // Just assert determinism and level sensitivity; collisions are legal.
        let k = row(vec![Value::Int64(42)]);
        assert_eq!(hash_key(&k, 0), hash_key(&k, 0));
        assert_ne!(hash_key(&k, 0), hash_key(&k, 1));
    }
}
