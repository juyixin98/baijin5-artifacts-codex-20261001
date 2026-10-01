//! Typed CSV fixture reader.
//!
//! Fixture contract (see `fixtures/README.md`):
//! * First line is a typed header: `name:bigint,label:text,flag:boolean`.
//! * One record per line; RFC-4180 quoting is supported (`""` escapes a quote,
//!   newlines allowed inside quotes). The parser works on raw bytes because
//!   every structural byte (`"`, `,`, CR, LF) is ASCII and therefore cannot
//!   appear inside a UTF-8 continuation sequence; each field is decoded as
//!   UTF-8 only after its bytes are complete, so multibyte text survives.
//! * NULL spelling: an empty unquoted field, or the literal `\N`.
//!   An empty *quoted* field (`""`) is the empty text string, not NULL.
//! * Output is split into Arrow2 chunks of at most `batch_rows` rows so the
//!   operator pipeline genuinely sees multiple input batches.

use std::fs::File;
use std::io::{BufReader, Read};
use std::path::Path;

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;

use crate::batch::TypedBatch;
use crate::batch::schema::{LogicalType, Schema};
use crate::error::{InputCode, Result, SetOpsError};

#[derive(Debug, Clone)]
struct Cell {
    text: String,
    quoted: bool,
}

/// Byte-oriented RFC-4180-ish record parser with a one-byte pushback slot.
struct CsvRecords<R: Read> {
    src: R,
    buf: Vec<u8>,
    pos: usize,
    end: usize,
    eof: bool,
    putback: Option<u8>,
    /// 1-based physical line of the most recently returned record.
    line: usize,
}

const READ_CAP: usize = 64 * 1024;

impl<R: Read> CsvRecords<R> {
    fn new(src: R) -> Self {
        Self {
            src,
            buf: vec![0u8; READ_CAP],
            pos: 0,
            end: 0,
            eof: false,
            putback: None,
            line: 0,
        }
    }

    fn fill(&mut self) -> std::io::Result<()> {
        if self.eof {
            return Ok(());
        }
        self.end = self.src.read(&mut self.buf)?;
        self.pos = 0;
        if self.end == 0 {
            self.eof = true;
        }
        Ok(())
    }

    fn next_byte(&mut self) -> std::io::Result<Option<u8>> {
        if let Some(b) = self.putback.take() {
            return Ok(Some(b));
        }
        if self.pos >= self.end {
            self.fill()?;
            if self.eof {
                return Ok(None);
            }
        }
        let b = self.buf[self.pos];
        self.pos += 1;
        Ok(Some(b))
    }

    fn unget(&mut self, b: u8) {
        debug_assert!(self.putback.is_none());
        self.putback = Some(b);
    }

    fn read_header_line(&mut self) -> Result<Option<String>> {
        self.line = 1;
        let mut raw: Vec<u8> = Vec::new();
        loop {
            match self.next_byte().map_err(io_err)? {
                None if raw.is_empty() => return Ok(None),
                None => return Ok(Some(decode(raw)?)),
                Some(b'\n') => {
                    if raw.last() == Some(&b'\r') {
                        raw.pop();
                    }
                    return Ok(Some(decode(raw)?));
                }
                Some(b) => raw.push(b),
            }
        }
    }

    /// Parse one record. Returns None at clean EOF; skips blank physical lines.
    fn next_record(&mut self) -> Result<Option<Vec<Cell>>> {
        let mut first = match self.next_byte().map_err(io_err)? {
            None => return Ok(None),
            Some(b) => b,
        };
        while first == b'\n' || first == b'\r' {
            if first == b'\n' {
                self.line += 1;
            }
            first = match self.next_byte().map_err(io_err)? {
                None => return Ok(None),
                Some(b) => b,
            };
        }
        self.line += 1;

        let mut fields: Vec<Cell> = Vec::new();
        let mut cur: Vec<u8> = Vec::new();
        let mut quoted = false;
        let mut ever_quoted = false;

        let mut b = first;
        loop {
            if b == b'"' && quoted {
                // "" inside a quoted field is an escaped quote; any other byte
                // closes the field and is replayed to the outer state machine.
                match self.next_byte().map_err(io_err)? {
                    Some(b'"') => cur.push(b'"'),
                    Some(other) => {
                        quoted = false;
                        self.unget(other);
                    }
                    None => {
                        fields.push(Cell {
                            text: decode(cur)?,
                            quoted: ever_quoted,
                        });
                        return Ok(Some(fields));
                    }
                }
            } else if b == b'"' && !quoted && cur.is_empty() {
                quoted = true;
                ever_quoted = true;
            } else if b == b',' && !quoted {
                fields.push(Cell {
                    text: decode(std::mem::take(&mut cur))?,
                    quoted: ever_quoted,
                });
                ever_quoted = false;
            } else if b == b'\n' && !quoted {
                if cur.last() == Some(&b'\r') {
                    cur.pop();
                }
                fields.push(Cell {
                    text: decode(cur)?,
                    quoted: ever_quoted,
                });
                return Ok(Some(fields));
            } else {
                if b == b'\n' {
                    self.line += 1;
                }
                cur.push(b);
            }
            b = match self.next_byte().map_err(io_err)? {
                None => {
                    fields.push(Cell {
                        text: decode(cur)?,
                        quoted: ever_quoted,
                    });
                    return Ok(Some(fields));
                }
                Some(x) => x,
            };
        }
    }
}

fn decode(raw: Vec<u8>) -> Result<String> {
    String::from_utf8(raw).map_err(|e| {
        SetOpsError::input(
            InputCode::ParseValue,
            format!("field is not valid UTF-8: {e}"),
        )
    })
}

fn io_err(e: std::io::Error) -> SetOpsError {
    SetOpsError::input(InputCode::ParseValue, format!("CSV read error: {e}"))
}

/// Streaming reader yielding typed batches.
pub struct TypedCsvReader<R: Read> {
    schema: Schema,
    parser: CsvRecords<R>,
    batch_rows: usize,
}

impl TypedCsvReader<BufReader<File>> {
    pub fn open(path: impl AsRef<Path>, batch_rows: usize) -> Result<Self> {
        let path = path.as_ref();
        let f = File::open(path).map_err(|e| {
            SetOpsError::input(
                InputCode::NotFound,
                format!("cannot open {}: {e}", path.display()),
            )
        })?;
        Self::from_read(BufReader::new(f), batch_rows)
            .map_err(|e| e.ctx(|c| c.at(path.display().to_string())))
    }
}

impl<R: Read> TypedCsvReader<R> {
    pub fn from_read(read: R, batch_rows: usize) -> Result<Self> {
        if batch_rows == 0 {
            return Err(SetOpsError::input(
                InputCode::InvalidRequest,
                "batch_rows must be > 0",
            ));
        }
        let mut parser = CsvRecords::new(read);
        let header = parser
            .read_header_line()?
            .ok_or_else(|| SetOpsError::input(InputCode::InvalidRequest, "empty CSV input"))?;
        let schema = Schema::parse_header(&header)?;
        Ok(Self {
            schema,
            parser,
            batch_rows,
        })
    }

    pub fn schema(&self) -> &Schema {
        &self.schema
    }

    pub fn read_all(&mut self) -> Result<Vec<TypedBatch>> {
        let mut out = Vec::new();
        while let Some(b) = self.next_batch()? {
            out.push(b);
        }
        Ok(out)
    }

    pub fn next_batch(&mut self) -> Result<Option<TypedBatch>> {
        let ncols = self.schema.len();
        let mut builders: Vec<ColumnBuilder> = self
            .schema
            .fields()
            .iter()
            .map(|f| ColumnBuilder::new(f.ty))
            .collect();
        let mut count = 0;
        while count < self.batch_rows {
            match self.parser.next_record()? {
                None => break,
                Some(fields) => {
                    let at_line = self.parser.line;
                    if fields.len() != ncols {
                        return Err(SetOpsError::input(
                            InputCode::ColumnCountMismatch,
                            format!(
                                "line {at_line}: record has {} fields, schema has {ncols}",
                                fields.len()
                            ),
                        )
                        .ctx(|c| c.line(at_line)));
                    }
                    for (i, cell) in fields.iter().enumerate() {
                        builders[i]
                            .push(cell)
                            .map_err(|e| e.ctx(|c| c.line(at_line)))?;
                    }
                    count += 1;
                }
            }
        }
        if count == 0 {
            return Ok(None);
        }
        let arrays = builders.into_iter().map(|b| b.finish()).collect();
        let chunk = Chunk::try_new(arrays)
            .map_err(|e| SetOpsError::compute(format!("arrow chunk error: {e}")))?;
        Ok(Some(TypedBatch::new(self.schema.clone(), chunk)))
    }
}

/// Read the whole file into batches (bounded chunks).
pub fn read_typed_csv_batches(
    path: impl AsRef<Path>,
    batch_rows: usize,
) -> Result<Vec<TypedBatch>> {
    TypedCsvReader::open(path, batch_rows)?.read_all()
}

/// Read the typed schema from the header plus all data batches. A file that
/// contains only a header yields an empty batch vector while still reporting
/// its schema, which empty-side set operations need.
pub fn read_typed_csv(
    path: impl AsRef<Path>,
    batch_rows: usize,
) -> Result<(Schema, Vec<TypedBatch>)> {
    let mut reader = TypedCsvReader::open(path, batch_rows)?;
    let schema = reader.schema().clone();
    let batches = reader.read_all()?;
    Ok((schema, batches))
}

enum ColumnBuilder {
    BigInt(Vec<Option<i64>>),
    Double(Vec<Option<f64>>),
    Boolean(Vec<Option<bool>>),
    Text(Vec<Option<String>>),
}

impl ColumnBuilder {
    fn new(ty: LogicalType) -> Self {
        match ty {
            LogicalType::BigInt => Self::BigInt(Vec::new()),
            LogicalType::Double => Self::Double(Vec::new()),
            LogicalType::Boolean => Self::Boolean(Vec::new()),
            LogicalType::Text => Self::Text(Vec::new()),
        }
    }

    fn push(&mut self, cell: &Cell) -> Result<()> {
        // `quoted == false` empty field, or explicit \N, means NULL.
        let is_null = !cell.quoted && (cell.text.is_empty() || cell.text == "\\N");
        let bad = |want: &str| {
            SetOpsError::input(
                InputCode::ParseValue,
                format!("'{:.20}' is not a {want}", cell.text),
            )
        };
        match self {
            Self::BigInt(v) => v.push(if is_null {
                None
            } else {
                Some(cell.text.parse().map_err(|_| bad("bigint"))?)
            }),
            Self::Double(v) => v.push(if is_null {
                None
            } else {
                Some(cell.text.parse().map_err(|_| bad("double"))?)
            }),
            Self::Boolean(v) => v.push(if is_null {
                None
            } else {
                Some(match cell.text.to_ascii_lowercase().as_str() {
                    "true" | "t" | "1" | "yes" | "y" => true,
                    "false" | "f" | "0" | "no" | "n" => false,
                    _ => return Err(bad("boolean")),
                })
            }),
            Self::Text(v) => v.push(if is_null {
                None
            } else {
                Some(cell.text.clone())
            }),
        }
        Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;

    const CSV: &str = "id:bigint,name:text,flag:boolean\n\
1,alice,true\n\
2,,false\n\
3,\"quoted, name\",\n\
4,\"quote \"\"here\"\"\",t\n";

    #[test]
    fn parses_typed_batches_with_nulls_and_quoting() {
        let mut r = TypedCsvReader::from_read(CSV.as_bytes(), 2).unwrap();
        let b1 = r.next_batch().unwrap().unwrap();
        let b2 = r.next_batch().unwrap().unwrap();
        assert!(r.next_batch().unwrap().is_none());
        assert_eq!(b1.rows(), 2);
        assert_eq!(b2.rows(), 2);

        let names1 = b1.chunk.arrays()[1]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .unwrap();
        assert_eq!(names1.value(0), "alice");
        assert!(names1.is_null(1));
        let flags = b2.chunk.arrays()[2]
            .as_any()
            .downcast_ref::<BooleanArray>()
            .unwrap();
        assert!(flags.value(1));
        assert!(flags.is_null(0));
        let names2 = b2.chunk.arrays()[1]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .unwrap();
        assert_eq!(names2.value(0), "quoted, name");
        assert_eq!(names2.value(1), "quote \"here\"");
    }

    #[test]
    fn utf8_multibyte_text_survives() {
        let csv = "s:text\nhéllo世界\n";
        let mut r = TypedCsvReader::from_read(csv.as_bytes(), 10).unwrap();
        let b = r.next_batch().unwrap().unwrap();
        let a = b.chunk.arrays()[0]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .unwrap();
        assert_eq!(a.value(0), "héllo世界");
    }

    #[test]
    fn quoted_empty_string_is_not_null() {
        let csv = "s:text\n\"\"\n";
        let mut r = TypedCsvReader::from_read(csv.as_bytes(), 10).unwrap();
        let b = r.next_batch().unwrap().unwrap();
        let a = b.chunk.arrays()[0]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .unwrap();
        assert!(!a.is_null(0));
        assert_eq!(a.value(0), "");
    }

    #[test]
    fn reports_column_count_and_parse_errors() {
        let csv = "a:bigint,b:bigint\n1\n";
        let err = TypedCsvReader::from_read(csv.as_bytes(), 10)
            .unwrap()
            .read_all()
            .unwrap_err();
        assert!(matches!(
            err.kind,
            crate::error::ErrorKind::Input(InputCode::ColumnCountMismatch)
        ));
        assert_eq!(err.context.line, Some(2));

        let csv = "a:bigint\nnope\n";
        let err = TypedCsvReader::from_read(csv.as_bytes(), 10)
            .unwrap()
            .read_all()
            .unwrap_err();
        assert!(matches!(
            err.kind,
            crate::error::ErrorKind::Input(InputCode::ParseValue)
        ));
    }
}
