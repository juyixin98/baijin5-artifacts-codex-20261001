//! Ordered, nullable value cells shared by grouping, spilling and aggregation.
//!
//! Every column the engine touches maps onto the same four-variant [`Cell`] so
//! that group keys, spilled records and aggregation inputs share one codec and
//! one total order.
//!
//! Ordering notes:
//! * [`Cell::Null`] sorts before every non-null value (SQL `NULLS FIRST` in
//!   the ascending order used internally).
//! * Two floats are ordered with [`f64::total_cmp`], giving a total order even
//!   for NaNs.  Hashing/equality use the bit pattern, so `-0.0` and `0.0` are
//!   distinct keys — matching raw Arrow2 storage; the synthetic fixtures never
//!   rely on the distinction.
//! * Cross-type order (`Null < I64 < F64 < Str`) exists only to make the order
//!   total.  A typed column never mixes numeric/string variants in practice.

use std::cmp::Ordering;
use std::hash::{Hash, Hasher};
use std::io::{self, Read, Write};

use crate::batch::{Column, DataType};

/// A nullable scalar value in one of the engine's physical types.
#[derive(Debug, Clone)]
pub enum Cell {
    Null,
    I64(i64),
    F64(f64),
    Str(String),
}

impl Cell {
    pub fn is_null(&self) -> bool {
        matches!(self, Cell::Null)
    }

    pub fn data_type(&self) -> Option<DataType> {
        match self {
            Cell::Null => None,
            Cell::I64(_) => Some(DataType::Int64),
            Cell::F64(_) => Some(DataType::Float64),
            Cell::Str(_) => Some(DataType::Utf8),
        }
    }

    /// Approximate retained heap size, used by the memory budget.  The string
    /// byte buffer is counted explicitly (the requirement that the string
    /// buffer is part of the budget).
    pub fn approx_bytes(&self) -> usize {
        const ENUM_SIZE: usize = 24; // discriminant + largest payload (String)
        match self {
            Cell::Str(s) => ENUM_SIZE + s.capacity(),
            _ => ENUM_SIZE,
        }
    }

    /// Read a typed cell from an Arrow2 column at `row`.
    pub fn from_column(col: &Column, row: usize) -> Cell {
        match col {
            Column::Int64(a) => match a.get(row) {
                Some(v) => Cell::I64(v),
                None => Cell::Null,
            },
            Column::Float64(a) => match a.get(row) {
                Some(v) => Cell::F64(v),
                None => Cell::Null,
            },
            Column::Utf8(a) => match a.get(row) {
                Some(v) => Cell::Str(v.to_string()),
                None => Cell::Null,
            },
        }
    }
}

impl PartialEq for Cell {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Cell::Null, Cell::Null) => true,
            (Cell::I64(a), Cell::I64(b)) => a == b,
            (Cell::F64(a), Cell::F64(b)) => a.to_bits() == b.to_bits(),
            (Cell::Str(a), Cell::Str(b)) => a == b,
            _ => false,
        }
    }
}

impl Eq for Cell {}

impl Hash for Cell {
    fn hash<H: Hasher>(&self, state: &mut H) {
        std::mem::discriminant(self).hash(state);
        match self {
            Cell::Null => {}
            Cell::I64(v) => v.hash(state),
            Cell::F64(v) => v.to_bits().hash(state),
            Cell::Str(v) => v.hash(state),
        }
    }
}

impl Ord for Cell {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Cell::Null, Cell::Null) => Ordering::Equal,
            (Cell::Null, _) => Ordering::Less,
            (_, Cell::Null) => Ordering::Greater,
            (Cell::I64(a), Cell::I64(b)) => a.cmp(b),
            (Cell::F64(a), Cell::F64(b)) => a.total_cmp(b),
            (Cell::Str(a), Cell::Str(b)) => a.cmp(b),
            // Cross-type ordering: discriminant rank.
            (a, b) => type_rank(a).cmp(&type_rank(b)),
        }
    }
}

impl PartialOrd for Cell {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

fn type_rank(c: &Cell) -> u8 {
    match c {
        Cell::Null => 0,
        Cell::I64(_) => 1,
        Cell::F64(_) => 2,
        Cell::Str(_) => 3,
    }
}

// ---------------------------------------------------------------------------
// Compact binary codec used by spill files and the persisted group table.
// ---------------------------------------------------------------------------

const TAG_NULL: u8 = 0;
const TAG_I64: u8 = 1;
const TAG_F64: u8 = 2;
const TAG_STR: u8 = 3;

pub fn write_cell<W: Write>(w: &mut W, cell: &Cell) -> io::Result<()> {
    match cell {
        Cell::Null => w.write_all(&[TAG_NULL]),
        Cell::I64(v) => {
            w.write_all(&[TAG_I64])?;
            w.write_all(&v.to_le_bytes())
        }
        Cell::F64(v) => {
            w.write_all(&[TAG_F64])?;
            w.write_all(&v.to_le_bytes())
        }
        Cell::Str(s) => {
            w.write_all(&[TAG_STR])?;
            let len = u32::try_from(s.len())
                .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "string too long"))?;
            w.write_all(&len.to_le_bytes())?;
            w.write_all(s.as_bytes())
        }
    }
}

pub fn read_cell<R: Read>(r: &mut R) -> io::Result<Cell> {
    let mut tag = [0u8; 1];
    r.read_exact(&mut tag)?;
    match tag[0] {
        TAG_NULL => Ok(Cell::Null),
        TAG_I64 => {
            let mut buf = [0u8; 8];
            r.read_exact(&mut buf)?;
            Ok(Cell::I64(i64::from_le_bytes(buf)))
        }
        TAG_F64 => {
            let mut buf = [0u8; 8];
            r.read_exact(&mut buf)?;
            Ok(Cell::F64(f64::from_le_bytes(buf)))
        }
        TAG_STR => {
            let mut len_buf = [0u8; 4];
            r.read_exact(&mut len_buf)?;
            let len = u32::from_le_bytes(len_buf) as usize;
            let mut buf = vec![0u8; len];
            r.read_exact(&mut buf)?;
            String::from_utf8(buf)
                .map(Cell::Str)
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
        }
        other => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("unknown cell tag {other}"),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cell_order_is_total_and_nulls_first() {
        let mut vals = [
            Cell::I64(3),
            Cell::Null,
            Cell::I64(1),
            Cell::I64(2),
            Cell::Null,
        ];
        vals.sort();
        assert!(vals[0].is_null());
        assert!(vals[1].is_null());
        assert_eq!(vals[2], Cell::I64(1));
    }

    #[test]
    fn float_order_handles_neg_zero_and_nan() {
        assert!(Cell::F64(f64::NAN) > Cell::F64(f64::INFINITY));
        assert!(Cell::F64(-0.0) < Cell::F64(0.0));
        // Bit-based equality distinguishes signed zero.
        assert_ne!(Cell::F64(-0.0), Cell::F64(0.0));
    }

    #[test]
    fn strings_sort_bytewise_and_account_bytes() {
        let mut v = [
            Cell::Str("banana".into()),
            Cell::Str("apple".into()),
            Cell::Str("cherry".into()),
        ];
        v.sort();
        assert_eq!(v[0], Cell::Str("apple".into()));
        assert!(Cell::Str("abc".to_string()).approx_bytes() >= 24 + 3);
    }

    #[test]
    fn codec_round_trips_every_variant() {
        for cell in [
            Cell::Null,
            Cell::I64(-42),
            Cell::F64(3.25),
            Cell::Str("héllo, world".into()),
        ] {
            let mut buf = Vec::new();
            write_cell(&mut buf, &cell).unwrap();
            let got = read_cell(&mut buf.as_slice()).unwrap();
            assert_eq!(got, cell);
        }
    }
}
