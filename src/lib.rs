//! RIB: RLE + bit-pack hybrid block codec for integer columns.
//!
//! Layout of the crate:
//! - [`format`]: binary block header layout and legality rules
//! - [`error`]: structured error categories with byte offsets
//! - [`bitstream`]: LSB-first bit writer / bounds-checked bit reader
//! - [`encode`]: encoding kernel (column -> blocks)
//! - [`decode`]: decoding kernel (blocks -> column), budget-checked
//! - [`budget`]: resource-control limits applied before decoding
//! - [`config`]: configuration layer (TOML file + env override)
//! - [`api`]: Axum HTTP service exposing the codec

pub mod api;
pub mod bitstream;
pub mod budget;
pub mod config;
pub mod decode;
pub mod encode;
pub mod error;
pub mod format;
