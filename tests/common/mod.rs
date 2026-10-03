//! Shared test harness: deterministic PRNG, scripted write-back adapter,
//! and the engine/model pair builder. The independent reference model lives
//! in `model.rs` and shares no logic with `src/`.
//!
//! Each integration-test binary compiles this module separately and uses a
//! different subset of it, hence the crate-level allow.

#![allow(dead_code)]

pub mod model;

use std::sync::Arc;

use arc_cache::engine::Engine;
use arc_cache::store::MemStore;
use arc_cache::writeback::Writeback;

pub use model::Model;

/// Deterministic xorshift64* PRNG (no external dependency).
pub struct XorShift(u64);

impl XorShift {
    pub fn new(seed: u64) -> Self {
        XorShift(seed | 1)
    }
    pub fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    pub fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

pub type WbScript = Arc<dyn Fn(u64) -> bool + Send + Sync>;

/// Write-back adapter that persists into a shared MemStore and fails
/// according to a deterministic script.
pub struct ScriptedWriteback {
    pub store: MemStore,
    pub fail: WbScript,
}

impl Writeback for ScriptedWriteback {
    fn writeback(&self, page: u64, data: &[u8]) -> Result<(), String> {
        if (self.fail)(page) {
            return Err(format!("scripted write-back failure on page {page}"));
        }
        self.store.put(page, data.to_vec());
        Ok(())
    }
}

/// A write-back script that fails for pages `≡ 3 (mod 5)`. Stateless (a
/// pure function of the page id) so the engine and the independent model
/// observe identical failures no matter how many times each consults it.
pub fn flaky_script() -> WbScript {
    Arc::new(|page| (page.wrapping_mul(3).wrapping_add(1)) % 5 == 0)
}

pub fn never_fail() -> WbScript {
    Arc::new(|_| false)
}

/// Deterministic fixture content for a page id.
pub fn fixture_content(page: u64) -> Vec<u8> {
    vec![(page % 251) as u8; 16]
}

#[derive(Debug, Clone)]
pub enum Step {
    Read(u64),
    Write(u64, Vec<u8>),
    Resize(usize),
}

/// Build an engine + independent model sharing identical store contents and
/// an equivalent write-back failure script. Pages `0..universe` are seeded
/// with `fixture_content`.
pub fn pair(capacity: usize, universe: u64, wb_fail: WbScript) -> (Engine, Model, MemStore) {
    let store = MemStore::new();
    let mut model_store = std::collections::HashMap::new();
    for page in 0..universe {
        let content = fixture_content(page);
        store.put(page, content.clone());
        model_store.insert(page, content);
    }
    let wb = ScriptedWriteback {
        store: store.clone(),
        fail: wb_fail.clone(),
    };
    let engine = Engine::new(capacity, Box::new(store.clone()), Box::new(wb), 64, 8, false);
    let model = Model::new(capacity, model_store, wb_fail);
    (engine, model, store)
}
