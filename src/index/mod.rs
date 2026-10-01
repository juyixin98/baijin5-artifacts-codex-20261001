//! Index layer: universe bitmaps, SQL 3VL, typed Arrow2 batches,
//! column-value bitmap indexes and versioned deletions.

pub mod batch;
pub mod bitmap;
pub mod tricolor;
pub mod value_index;
pub mod version;

pub use batch::{ColumnMeta, LogicalType, TypedTable};
pub use bitmap::Bitmap;
pub use tricolor::{Tri, Tricolor};
pub use value_index::{CmpOp, ColumnIndex, Scalar};
pub use version::{Version, VersionMap};
