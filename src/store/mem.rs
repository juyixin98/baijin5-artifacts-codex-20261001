//! Volatile in-memory backing store. Primary reusable test fixture; also
//! handy for embedding the model without touching disk.

use super::{BackingStore, StoreError};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone, Default)]
pub struct MemStore {
    files: Arc<Mutex<HashMap<String, Vec<u8>>>>,
}

impl MemStore {
    pub fn new() -> Self {
        Self::default()
    }
}

impl BackingStore for MemStore {
    fn create(&self, path: &str, size: u64) -> Result<(), StoreError> {
        let mut files = self.files.lock().unwrap();
        if files.contains_key(path) {
            return Err(StoreError::Io(format!("file already exists: {path}")));
        }
        files.insert(path.to_string(), vec![0u8; size as usize]);
        Ok(())
    }

    fn exists(&self, path: &str) -> bool {
        self.files.lock().unwrap().contains_key(path)
    }

    fn size(&self, path: &str) -> Result<u64, StoreError> {
        self.files
            .lock()
            .unwrap()
            .get(path)
            .map(|f| f.len() as u64)
            .ok_or_else(|| StoreError::NotFound(path.to_string()))
    }

    fn read_at(&self, path: &str, offset: u64, len: usize) -> Result<Vec<u8>, StoreError> {
        let files = self.files.lock().unwrap();
        let data = files
            .get(path)
            .ok_or_else(|| StoreError::NotFound(path.to_string()))?;
        let start = offset as usize;
        if start >= data.len() {
            return Ok(Vec::new());
        }
        let end = (start + len).min(data.len());
        Ok(data[start..end].to_vec())
    }

    fn write_at(&self, path: &str, offset: u64, data: &[u8]) -> Result<(), StoreError> {
        let mut files = self.files.lock().unwrap();
        let file = files
            .get_mut(path)
            .ok_or_else(|| StoreError::NotFound(path.to_string()))?;
        let start = offset as usize;
        let end = start + data.len();
        if end > file.len() {
            file.resize(end, 0); // sparse extension reads as zeros
        }
        file[start..end].copy_from_slice(data);
        Ok(())
    }

    fn truncate(&self, path: &str, size: u64) -> Result<(), StoreError> {
        let mut files = self.files.lock().unwrap();
        let file = files
            .get_mut(path)
            .ok_or_else(|| StoreError::NotFound(path.to_string()))?;
        file.resize(size as usize, 0);
        Ok(())
    }

    fn flush(&self, path: &str) -> Result<(), StoreError> {
        if self.files.lock().unwrap().contains_key(path) {
            Ok(())
        } else {
            Err(StoreError::NotFound(path.to_string()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sparse_extension_reads_as_zeros() {
        let s = MemStore::new();
        s.create("f", 4).unwrap();
        s.write_at("f", 8, &[1, 2]).unwrap();
        assert_eq!(s.size("f").unwrap(), 10);
        assert_eq!(
            s.read_at("f", 0, 10).unwrap(),
            vec![0, 0, 0, 0, 0, 0, 0, 0, 1, 2]
        );
    }

    #[test]
    fn short_read_at_eof() {
        let s = MemStore::new();
        s.create("f", 3).unwrap();
        assert_eq!(s.read_at("f", 2, 10).unwrap(), vec![0]);
        assert_eq!(s.read_at("f", 3, 10).unwrap(), Vec::<u8>::new());
    }

    #[test]
    fn truncate_shrinks_and_grows() {
        let s = MemStore::new();
        s.create("f", 4).unwrap();
        s.write_at("f", 0, &[9, 9, 9, 9]).unwrap();
        s.truncate("f", 2).unwrap();
        assert_eq!(s.read_at("f", 0, 4).unwrap(), vec![9, 9]);
        s.truncate("f", 5).unwrap();
        assert_eq!(s.read_at("f", 0, 5).unwrap(), vec![9, 9, 0, 0, 0]);
    }

    #[test]
    fn missing_file_is_not_found() {
        let s = MemStore::new();
        assert_eq!(
            s.read_at("nope", 0, 1).unwrap_err(),
            StoreError::NotFound("nope".to_string())
        );
    }
}
