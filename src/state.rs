//! 跨进程的采样/持久状态。
//!
//! **持久化什么**：只持久化少量“采样状态”——自适应目标 `p` 与累计统计；
//! **不持久化**列表成员与页内容（幽灵/真实列表在重启后冷启动，
//! 这是明确的设计限制，见 `README.md`）。因此快照文件里不出现页内容。
//!
//! 快照原子落盘（临时文件 + rename），可在每次 ghost hit 后或关闭时保存。
//! 文件只含非敏感元数据（容量、p、各列表长度、计数）。

use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::types::CacheStats;

/// 跨进程采样状态。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SampledState {
    /// 格式版本，便于未来兼容迁移。
    pub version: u32,
    pub c: usize,
    pub p: usize,
    pub stats: CacheStats,
}

impl SampledState {
    pub const VERSION: u32 = 1;

    pub fn new(c: usize, p: usize, stats: CacheStats) -> Self {
        Self {
            version: Self::VERSION,
            c,
            p,
            stats,
        }
    }

    /// 原子写盘（tmp + rename），只含元数据。
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let file_name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("state.json");
        let tmp = path.with_file_name(format!(".{file_name}.tmp"));
        std::fs::write(&tmp, json)?;
        std::fs::rename(&tmp, path)?;
        Ok(())
    }

    /// 读回采样状态；文件不存在返回 `Ok(None)`（冷启动）。
    pub fn load(path: &Path) -> std::io::Result<Option<Self>> {
        match std::fs::read_to_string(path) {
            Ok(text) => {
                let s: SampledState = serde_json::from_str(&text)
                    .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
                Ok(Some(s))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// 版本不匹配时按冷启动处理（保守、不猜测旧格式语义）。
    pub fn supported(&self) -> bool {
        self.version == Self::VERSION
    }
}
