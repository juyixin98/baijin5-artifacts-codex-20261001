//! 页存储与回写适配器。
//!
//! [`PageStore`] 是缓存与“后端存储”之间的**唯一边界**：
//! - 缺页/幽灵命中时通过 [`PageStore::fetch`] 装入页内容；
//! - 淘汰脏页时通过 [`PageStore::write_back`] 回写。
//!
//! 算法层（[`crate::arc`]）完全不感知本 trait，因此可以在测试中注入
//! [`FaultStore`] 精确构造“脏页回写失败轨迹”，也可以提供独立于引擎的
//! 参考模型夹具。

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use crate::types::{PageId, WriteBackError};

/// 页内容。合成夹具使用确定性内容（页号派生），不涉及真实业务数据。
pub type PageData = Vec<u8>;

/// 后端存储适配器。
pub trait PageStore: Send {
    /// 从后端装入一页。
    fn fetch(&mut self, page: PageId) -> Result<PageData, StoreError>;

    /// 把脏页回写后端。返回错误时，调用方（引擎）保留该页驻留且仍为脏，
    /// 缓存状态不发生改变（plan 被丢弃）。
    fn write_back(&mut self, page: PageId, data: &[u8]) -> Result<(), StoreError>;
}

#[derive(Debug)]
pub enum StoreError {
    Io(String, WriteBackError),
    Missing(PageId),
}

impl StoreError {
    pub fn kind(&self) -> WriteBackError {
        match self {
            StoreError::Io(_, k) => *k,
            // “页不存在”按永久失败处理：重试不会让它出现。
            StoreError::Missing(_) => WriteBackError::Permanent,
        }
    }
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StoreError::Io(msg, kind) => write!(f, "store I/O error ({kind}): {msg}"),
            StoreError::Missing(p) => write!(f, "page missing in backing store: {p}"),
        }
    }
}

impl std::error::Error for StoreError {}

/// 文件系统后端：每页一个文件，`<root>/pages/<id>.page`。
///
/// 页内容为合成数据。缺失页在首次 `fetch` 时按页号确定性生成并落盘，
/// 从而无需任何预置业务数据即可回放。
pub struct FilePageStore {
    root: PathBuf,
    page_size: usize,
}

impl FilePageStore {
    pub fn new(root: impl Into<PathBuf>, page_size: usize) -> std::io::Result<Self> {
        let root = root.into();
        std::fs::create_dir_all(root.join("pages"))?;
        Ok(Self { root, page_size })
    }

    fn path_for(&self, page: PageId) -> PathBuf {
        self.root
            .join("pages")
            .join(format!("{}.page", page.as_u64()))
    }

    /// 合成页内容：可识别但不含任何敏感信息的确定性字节模式。
    pub(crate) fn synthesize(page: PageId, page_size: usize) -> PageData {
        let seed = page
            .as_u64()
            .wrapping_mul(0x9E37_79B9_7F4A_7C15)
            .to_le_bytes();
        let mut buf = Vec::with_capacity(page_size);
        let mut b = seed;
        while buf.len() < page_size {
            // xorshift 派生，纯确定性。
            let mut x = u64::from_le_bytes(b);
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            b = x.to_le_bytes();
            buf.extend_from_slice(&b);
        }
        buf.truncate(page_size);
        buf
    }
}

impl PageStore for FilePageStore {
    fn fetch(&mut self, page: PageId) -> Result<PageData, StoreError> {
        let path = self.path_for(page);
        match std::fs::read(&path) {
            Ok(bytes) => Ok(bytes),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let data = Self::synthesize(page, self.page_size);
                std::fs::write(&path, &data)
                    .map_err(|e| StoreError::Io(e.to_string(), WriteBackError::Transient))?;
                Ok(data)
            }
            Err(e) => Err(StoreError::Io(e.to_string(), WriteBackError::Transient)),
        }
    }

    fn write_back(&mut self, page: PageId, data: &[u8]) -> Result<(), StoreError> {
        // 先写临时文件再原子改名，避免崩溃留下半页。
        let target = self.path_for(page);
        let tmp = self
            .root
            .join("pages")
            .join(format!("{}.page.tmp", page.as_u64()));
        if let Err(e) = std::fs::write(&tmp, data) {
            return Err(StoreError::Io(e.to_string(), WriteBackError::Transient));
        }
        std::fs::rename(&tmp, &target)
            .map_err(|e| StoreError::Io(e.to_string(), WriteBackError::Transient))
    }
}

/// 内存后端 + 可注入故障，用于回写失败轨迹测试和独立模型夹具。
///
/// 注意：它与缓存引擎完全独立，不依赖被测核心的任何类型行为，
/// 因此独立参考模型可以共享它而不构成“自我作证”。
pub struct FaultStore {
    pages: HashMap<PageId, PageData>,
    page_size: usize,
    /// 按页配置的回写故障计划：每次 write_back 弹出一个错误类别。
    /// 队列耗尽后该页回写成功。
    fault_plan: Mutex<BTreeMap<PageId, Vec<WriteBackError>>>,
    /// 已成功回写的页（按调用顺序记录，供断言失败轨迹）。
    pub written: Mutex<Vec<(PageId, PageData)>>,
    /// fetch 调用记录（用于断言“幽灵命中也必须重新装入”）。
    pub fetches: Mutex<Vec<PageId>>,
}

impl FaultStore {
    pub fn new(page_size: usize) -> Self {
        Self {
            pages: HashMap::new(),
            page_size,
            fault_plan: Mutex::new(BTreeMap::new()),
            written: Mutex::new(Vec::new()),
            fetches: Mutex::new(Vec::new()),
        }
    }

    /// 预置后端已有页内容。
    pub fn with_page(mut self, page: PageId, data: PageData) -> Self {
        self.pages.insert(page, data);
        self
    }

    /// 让某页的下一次回写失败（可连续注入多次）。
    pub fn inject_fault(&self, page: PageId, kind: WriteBackError) {
        self.fault_plan
            .lock()
            .unwrap()
            .entry(page)
            .or_default()
            .push(kind);
    }

    pub fn written_pages(&self) -> Vec<PageId> {
        self.written
            .lock()
            .unwrap()
            .iter()
            .map(|(p, _)| *p)
            .collect()
    }

    pub fn fetch_count(&self, page: PageId) -> usize {
        self.fetches
            .lock()
            .unwrap()
            .iter()
            .filter(|p| **p == page)
            .count()
    }

    fn synthesize(&self, page: PageId) -> PageData {
        FilePageStore::synthesize(page, self.page_size)
    }
}

impl PageStore for FaultStore {
    fn fetch(&mut self, page: PageId) -> Result<PageData, StoreError> {
        self.fetches.lock().unwrap().push(page);
        if let Some(data) = self.pages.get(&page) {
            return Ok(data.clone());
        }
        let data = self.synthesize(page);
        self.pages.insert(page, data.clone());
        Ok(data)
    }

    fn write_back(&mut self, page: PageId, data: &[u8]) -> Result<(), StoreError> {
        let next_fault = {
            let mut plan = self.fault_plan.lock().unwrap();
            plan.get_mut(&page).and_then(|q| {
                if q.is_empty() {
                    None
                } else {
                    Some(q.remove(0))
                }
            })
        };
        if let Some(kind) = next_fault {
            return Err(StoreError::Io("injected fault".to_string(), kind));
        }
        self.written.lock().unwrap().push((page, data.to_vec()));
        self.pages.insert(page, data.to_vec());
        Ok(())
    }
}

/// 辅助：返回绝对路径字符串（诊断展示时避免相对路径歧义）。
pub fn absolute_path_string(path: &Path) -> String {
    std::fs::canonicalize(path)
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| path.display().to_string())
}
