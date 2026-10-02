//! The page-model core: file objects, mappings, and the [`Vm`] runtime.

mod file;
mod mapping;
mod vm;

pub use file::{FileState, PageInfo, PageValidity};
pub use mapping::{MapKind, MappingId, MappingState};
pub use vm::{DiscardedPage, Stats, SyncOutcome, TruncateReport, UnmapReport, Vm};
