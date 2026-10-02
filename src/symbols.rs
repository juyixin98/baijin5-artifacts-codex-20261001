//! Symbol resolution with stable address identity.
//!
//! Contract: a frame's identity is `(module, addr)` — always. A symbol name
//! is display metadata attached to an identity, never the identity itself.
//! Two functions that happen to share a name (different modules, or static
//! duplicates at different addresses) therefore never merge. Frames with no
//! covering symbol keep a stable `0x<addr>` identity instead of being
//! dropped or guessed.

use std::collections::HashMap;

use serde::Serialize;

use crate::error::BackendError;
use crate::model::{FrameInput, SymbolEntry};

/// Identity key for tree nodes and flat profiles.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
pub struct FrameKey {
    pub module: String,
    pub addr: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ResolvedFrame {
    pub key: FrameKey,
    /// Symbol name when one was resolved; `None` for unknown addresses.
    pub name: Option<String>,
}

impl ResolvedFrame {
    /// Raw display form before run-level disambiguation (see `naming`).
    pub fn base_name(&self) -> String {
        self.name.clone().unwrap_or_else(|| format!("0x{:x}", self.key.addr))
    }
}

#[derive(Debug)]
pub struct SymbolTable {
    entries: Vec<SymbolEntry>,
}

impl SymbolTable {
    /// Validates ranges; rejects overlaps within a module.
    pub fn build(entries: Vec<SymbolEntry>) -> Result<Self, BackendError> {
        for e in &entries {
            if e.start >= e.end {
                return Err(BackendError::input(
                    "bad_symbol_range",
                    format!("symbol {}@{}: start {:#x} must be < end {:#x}", e.name, e.module, e.start, e.end),
                ));
            }
        }
        let mut by_module: HashMap<&str, Vec<&SymbolEntry>> = HashMap::new();
        for e in &entries {
            by_module.entry(e.module.as_str()).or_default().push(e);
        }
        for (module, mut list) in by_module {
            list.sort_by_key(|e| e.start);
            for pair in list.windows(2) {
                if pair[1].start < pair[0].end {
                    return Err(BackendError::input(
                        "symbol_overlap",
                        format!(
                            "module {}: symbols {} and {} overlap",
                            module, pair[0].name, pair[1].name
                        ),
                    ));
                }
            }
        }
        Ok(Self { entries })
    }

    /// Resolves one captured frame to its stable identity.
    ///
    /// - Module hint present: only that module's symbols are consulted; the
    ///   hint is kept in the identity even when no symbol covers the address.
    /// - No hint: a unique covering symbol across all modules supplies both
    ///   module and name. Zero or multiple matches keep the address identity
    ///   under module `"?"` — ambiguous frames are never merged by guessing.
    pub fn resolve(&self, frame: &FrameInput) -> ResolvedFrame {
        match &frame.module {
            Some(module) => {
                let name = self
                    .entries
                    .iter()
                    .find(|e| &e.module == module && e.start <= frame.addr && frame.addr < e.end)
                    .map(|e| e.name.clone());
                ResolvedFrame { key: FrameKey { module: module.clone(), addr: frame.addr }, name }
            }
            None => {
                let mut hits = self
                    .entries
                    .iter()
                    .filter(|e| e.start <= frame.addr && frame.addr < e.end);
                match (hits.next(), hits.next()) {
                    (Some(e), None) => ResolvedFrame {
                        key: FrameKey { module: e.module.clone(), addr: frame.addr },
                        name: Some(e.name.clone()),
                    },
                    // Zero hits (unknown) or multiple (ambiguous): stable
                    // address identity, no name, no merge.
                    _ => ResolvedFrame {
                        key: FrameKey { module: "?".into(), addr: frame.addr },
                        name: None,
                    },
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table() -> SymbolTable {
        SymbolTable::build(vec![
            SymbolEntry { module: "app".into(), name: "helper".into(), start: 0x4000, end: 0x4100 },
            SymbolEntry { module: "lib".into(), name: "helper".into(), start: 0x5000, end: 0x5100 },
        ])
        .unwrap()
    }

    #[test]
    fn same_name_in_different_modules_stays_distinct() {
        let t = table();
        let a = t.resolve(&FrameInput { addr: 0x4000, module: None });
        let b = t.resolve(&FrameInput { addr: 0x5000, module: None });
        assert_eq!(a.name.as_deref(), Some("helper"));
        assert_eq!(b.name.as_deref(), Some("helper"));
        assert_ne!(a.key, b.key, "same-named functions must not merge");
        assert_eq!(a.key.module, "app");
        assert_eq!(b.key.module, "lib");
    }

    #[test]
    fn missing_symbol_uses_stable_address_identity() {
        let t = table();
        let f = t.resolve(&FrameInput { addr: 0xdead, module: None });
        assert_eq!(f.name, None);
        assert_eq!(f.key, FrameKey { module: "?".into(), addr: 0xdead });
        assert_eq!(f.base_name(), "0xdead");
    }

    #[test]
    fn module_hint_is_kept_even_without_symbol() {
        let t = table();
        let f = t.resolve(&FrameInput { addr: 0x9999, module: Some("drv".into()) });
        assert_eq!(f.key.module, "drv");
        assert_eq!(f.name, None);
    }

    #[test]
    fn overlapping_symbols_rejected_as_input_error() {
        let err = SymbolTable::build(vec![
            SymbolEntry { module: "m".into(), name: "a".into(), start: 0, end: 10 },
            SymbolEntry { module: "m".into(), name: "b".into(), start: 5, end: 15 },
        ])
        .unwrap_err();
        assert_eq!(err.category, crate::error::ErrorCategory::Input);
        assert_eq!(err.code, "symbol_overlap");
    }

    #[test]
    fn empty_range_rejected() {
        let err = SymbolTable::build(vec![SymbolEntry {
            module: "m".into(),
            name: "a".into(),
            start: 10,
            end: 10,
        }])
        .unwrap_err();
        assert_eq!(err.code, "bad_symbol_range");
    }
}
