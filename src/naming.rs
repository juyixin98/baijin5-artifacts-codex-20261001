//! Run-level display-name assignment.
//!
//! Tree identity is `(module, addr)`, but folded stacks and audit paths are
//! read by humans. When one base name maps to several distinct identities
//! (same-named functions, stripped duplicates), the namer disambiguates the
//! *display* only — `helper@app` vs `helper@lib` — without touching identity.

use std::collections::HashMap;

use crate::symbols::{FrameKey, ResolvedFrame};

pub struct Namer {
    names: HashMap<FrameKey, String>,
}

impl Namer {
    pub fn build<'a>(frames: impl Iterator<Item = &'a ResolvedFrame>) -> Self {
        let mut base: HashMap<FrameKey, String> = HashMap::new();
        for f in frames {
            base.entry(f.key.clone()).or_insert_with(|| f.base_name());
        }
        let mut base_count: HashMap<&str, usize> = HashMap::new();
        for b in base.values() {
            *base_count.entry(b.as_str()).or_default() += 1;
        }
        let mut names = HashMap::new();
        let mut candidate_count: HashMap<String, usize> = HashMap::new();
        for (key, b) in &base {
            let display = if base_count[b.as_str()] == 1 {
                b.clone()
            } else {
                format!("{}@{}", b, key.module)
            };
            *candidate_count.entry(display.clone()).or_default() += 1;
            names.insert(key.clone(), display);
        }
        // Final tie-break for same module + same name at different addresses.
        for (key, display) in names.iter_mut() {
            if candidate_count[display.as_str()] > 1 {
                *display = format!("{}:0x{:x}", display, key.addr);
            }
        }
        Self { names }
    }

    pub fn name(&self, key: &FrameKey) -> String {
        self.names
            .get(key)
            .cloned()
            .unwrap_or_else(|| format!("0x{:x}", key.addr))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(module: &str, addr: u64, name: Option<&str>) -> ResolvedFrame {
        ResolvedFrame {
            key: FrameKey { module: module.into(), addr },
            name: name.map(|n| n.into()),
        }
    }

    #[test]
    fn duplicate_names_get_module_suffix() {
        let frames = [
            frame("app", 0x4000, Some("helper")),
            frame("lib", 0x5000, Some("helper")),
            frame("?", 0xdead, None),
        ];
        let namer = Namer::build(frames.iter());
        assert_eq!(namer.name(&frames[0].key), "helper@app");
        assert_eq!(namer.name(&frames[1].key), "helper@lib");
        assert_eq!(namer.name(&frames[2].key), "0xdead");
    }

    #[test]
    fn unique_names_stay_bare() {
        let frames = [frame("app", 1, Some("main")), frame("app", 2, Some("leaf"))];
        let namer = Namer::build(frames.iter());
        assert_eq!(namer.name(&frames[0].key), "main");
        assert_eq!(namer.name(&frames[1].key), "leaf");
    }
}
