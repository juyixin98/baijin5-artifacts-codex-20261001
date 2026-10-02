//! Fault-injecting store decorator. Reusable fixture for verifying that
//! failed syncs keep dirty marks and surface `SyncFailed`.

use super::{BackingStore, StoreError};
use std::sync::{Arc, Mutex};

#[derive(Debug, Default)]
struct FaultState {
    fail_next_writes: usize,
    fail_next_flushes: usize,
}

/// Cloneable handle: clone it before handing it to the model so tests can
/// arm faults while the model owns a copy.
#[derive(Debug, Clone, Default)]
pub struct FaultyStore<S> {
    inner: S,
    state: Arc<Mutex<FaultState>>,
}

impl<S> FaultyStore<S> {
    pub fn new(inner: S) -> Self {
        FaultyStore {
            inner,
            state: Arc::new(Mutex::new(FaultState::default())),
        }
    }

    /// The next `n` `write_at` calls fail with `StoreError::Injected`.
    pub fn arm_write_failures(&self, n: usize) {
        self.state.lock().unwrap().fail_next_writes = n;
    }

    /// The next `n` `flush` calls fail with `StoreError::Injected`.
    pub fn arm_flush_failures(&self, n: usize) {
        self.state.lock().unwrap().fail_next_flushes = n;
    }

    pub fn inner(&self) -> &S {
        &self.inner
    }
}

impl<S: BackingStore> BackingStore for FaultyStore<S> {
    fn create(&self, path: &str, size: u64) -> Result<(), StoreError> {
        self.inner.create(path, size)
    }

    fn exists(&self, path: &str) -> bool {
        self.inner.exists(path)
    }

    fn size(&self, path: &str) -> Result<u64, StoreError> {
        self.inner.size(path)
    }

    fn read_at(&self, path: &str, offset: u64, len: usize) -> Result<Vec<u8>, StoreError> {
        self.inner.read_at(path, offset, len)
    }

    fn write_at(&self, path: &str, offset: u64, data: &[u8]) -> Result<(), StoreError> {
        {
            let mut st = self.state.lock().unwrap();
            if st.fail_next_writes > 0 {
                st.fail_next_writes -= 1;
                return Err(StoreError::Injected(format!(
                    "write_at({path}, {offset}, {} bytes)",
                    data.len()
                )));
            }
        }
        self.inner.write_at(path, offset, data)
    }

    fn truncate(&self, path: &str, size: u64) -> Result<(), StoreError> {
        self.inner.truncate(path, size)
    }

    fn flush(&self, path: &str) -> Result<(), StoreError> {
        {
            let mut st = self.state.lock().unwrap();
            if st.fail_next_flushes > 0 {
                st.fail_next_flushes -= 1;
                return Err(StoreError::Injected(format!("flush({path})")));
            }
        }
        self.inner.flush(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::MemStore;

    #[test]
    fn armed_failures_are_consumed() {
        let s = FaultyStore::new(MemStore::new());
        s.create("f", 4).unwrap();
        s.arm_write_failures(2);
        assert!(matches!(
            s.write_at("f", 0, &[1]),
            Err(StoreError::Injected(_))
        ));
        assert!(matches!(
            s.write_at("f", 0, &[1]),
            Err(StoreError::Injected(_))
        ));
        s.write_at("f", 0, &[1]).unwrap(); // third call goes through
        assert_eq!(s.read_at("f", 0, 1).unwrap(), vec![1]);
    }

    #[test]
    fn clone_shares_fault_state() {
        let s = FaultyStore::new(MemStore::new());
        let handle = s.clone();
        s.create("f", 4).unwrap();
        handle.arm_flush_failures(1);
        assert!(matches!(s.flush("f"), Err(StoreError::Injected(_))));
        s.flush("f").unwrap();
    }
}
