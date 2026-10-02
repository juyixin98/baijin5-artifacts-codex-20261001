//! Filesystem backing store: real files under a configured root directory.
//! This is what the diagnostic server persists to.

use super::{BackingStore, StoreError};
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone)]
pub struct FsStore {
    root: PathBuf,
}

impl FsStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        FsStore { root: root.into() }
    }

    /// Resolve a caller-supplied relative path under the root, rejecting
    /// absolute paths and `..` traversal.
    fn resolve(&self, path: &str) -> Result<PathBuf, StoreError> {
        let rel = Path::new(path);
        if rel.is_absolute() || path.is_empty() {
            return Err(StoreError::InvalidPath(path.to_string()));
        }
        for c in rel.components() {
            match c {
                Component::Normal(_) | Component::CurDir => {}
                _ => return Err(StoreError::InvalidPath(path.to_string())),
            }
        }
        Ok(self.root.join(rel))
    }
}

impl BackingStore for FsStore {
    fn create(&self, path: &str, size: u64) -> Result<(), StoreError> {
        let p = self.resolve(path)?;
        if p.exists() {
            return Err(StoreError::Io(format!("file already exists: {path}")));
        }
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).map_err(|e| StoreError::Io(e.to_string()))?;
        }
        let f = std::fs::File::create(&p).map_err(|e| StoreError::Io(e.to_string()))?;
        f.set_len(size).map_err(|e| StoreError::Io(e.to_string()))
    }

    fn exists(&self, path: &str) -> bool {
        self.resolve(path).map(|p| p.exists()).unwrap_or(false)
    }

    fn size(&self, path: &str) -> Result<u64, StoreError> {
        let p = self.resolve(path)?;
        std::fs::metadata(&p)
            .map(|m| m.len())
            .map_err(|_| StoreError::NotFound(path.to_string()))
    }

    fn read_at(&self, path: &str, offset: u64, len: usize) -> Result<Vec<u8>, StoreError> {
        use std::os::unix::fs::FileExt;
        let p = self.resolve(path)?;
        let f = std::fs::File::open(&p).map_err(|_| StoreError::NotFound(path.to_string()))?;
        let mut buf = vec![0u8; len];
        let n = f
            .read_at(&mut buf, offset)
            .map_err(|e| StoreError::Io(e.to_string()))?;
        buf.truncate(n);
        Ok(buf)
    }

    fn write_at(&self, path: &str, offset: u64, data: &[u8]) -> Result<(), StoreError> {
        use std::os::unix::fs::FileExt;
        let p = self.resolve(path)?;
        let f = std::fs::OpenOptions::new()
            .write(true)
            .open(&p)
            .map_err(|_| StoreError::NotFound(path.to_string()))?;
        f.write_all_at(data, offset)
            .map_err(|e| StoreError::Io(e.to_string()))
    }

    fn truncate(&self, path: &str, size: u64) -> Result<(), StoreError> {
        let p = self.resolve(path)?;
        let f = std::fs::OpenOptions::new()
            .write(true)
            .open(&p)
            .map_err(|_| StoreError::NotFound(path.to_string()))?;
        f.set_len(size).map_err(|e| StoreError::Io(e.to_string()))
    }

    fn flush(&self, path: &str) -> Result<(), StoreError> {
        let p = self.resolve(path)?;
        let f = std::fs::File::open(&p).map_err(|_| StoreError::NotFound(path.to_string()))?;
        f.sync_all().map_err(|e| StoreError::Io(e.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "mmap-model-fsstore-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn roundtrip_and_persistence() {
        let root = temp_root("roundtrip");
        let s = FsStore::new(&root);
        s.create("sub/f.bin", 8).unwrap();
        s.write_at("sub/f.bin", 2, &[1, 2, 3]).unwrap();
        assert_eq!(
            s.read_at("sub/f.bin", 0, 8).unwrap(),
            vec![0, 0, 1, 2, 3, 0, 0, 0]
        );
        s.truncate("sub/f.bin", 4).unwrap();
        assert_eq!(s.size("sub/f.bin").unwrap(), 4);
        s.flush("sub/f.bin").unwrap();
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn rejects_traversal() {
        let root = temp_root("traversal");
        let s = FsStore::new(&root);
        assert!(matches!(
            s.create("../escape", 1),
            Err(StoreError::InvalidPath(_))
        ));
        assert!(matches!(
            s.create("/abs", 1),
            Err(StoreError::InvalidPath(_))
        ));
        std::fs::remove_dir_all(&root).unwrap();
    }
}
