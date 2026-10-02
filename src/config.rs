//! 外部配置：TOML 文件解析与校验。
//!
//! 配置与代码、测试独立组织：`config/` 下存放示例/夹具配置，
//! 运行时通过 `--config <path>` 指定。容量为零是**有定义的合法配置**：
//! 表示“缓存层关闭”，所有访问直接穿透后端（见 [`ArcConfig`] 文档）。

use std::path::Path;

/// ARC 缓存配置。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArcConfig {
    /// 缓存容量 c（按页计，c >= 0）。
    ///
    /// - `c == 0`：缓存关闭，不接受任何页驻留，访问计数但不产生淘汰；
    /// - `c > 0`：标准 ARC 语义，真实列表 T1∪T2 至多容纳 c 页。
    pub capacity: usize,
    /// 页大小（字节），仅用于文件系统布局与诊断展示，不影响替换算法。
    pub page_size: usize,
    /// 脏页回写失败的最大重试次数（0 表示不重试，直接判定失败）。
    pub writeback_retries: u32,
}

impl Default for ArcConfig {
    fn default() -> Self {
        Self {
            capacity: 8,
            page_size: 4096,
            writeback_retries: 0,
        }
    }
}

impl ArcConfig {
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.page_size == 0 {
            return Err(ConfigError::InvalidField(
                "page_size must be > 0".to_string(),
            ));
        }
        // capacity == 0 合法（缓存关闭），不报错。
        Ok(())
    }

    /// 从 TOML 文本解析。未知字段被拒绝，避免配置拼写错误被静默忽略。
    pub fn from_toml_str(text: &str) -> Result<Self, ConfigError> {
        let raw: RawConfig = toml::from_str(text).map_err(ConfigError::Parse)?;
        let cfg = ArcConfig {
            capacity: raw.cache.capacity,
            page_size: raw.cache.page_size,
            writeback_retries: raw.cache.writeback_retries,
        };
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn from_toml_path(path: &Path) -> Result<Self, ConfigError> {
        let text =
            std::fs::read_to_string(path).map_err(|e| ConfigError::Read(path.to_owned(), e))?;
        Self::from_toml_str(&text)
    }
}

#[derive(serde::Deserialize)]
struct RawCache {
    capacity: usize,
    page_size: usize,
    #[serde(default)]
    writeback_retries: u32,
}

#[derive(serde::Deserialize)]
struct RawConfig {
    cache: RawCache,
}

/// 服务器部分配置（HTTP 层使用），与算法配置分开校验。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServerConfig {
    pub bind: String,
    /// 脏页/快照落盘根目录。
    pub data_dir: String,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            bind: "127.0.0.1:8080".to_string(),
            data_dir: "./var/arc-cache".to_string(),
        }
    }
}

impl ServerConfig {
    pub fn from_toml_str(text: &str) -> Result<Self, ConfigError> {
        let raw: RawServer = toml::from_str(text).map_err(ConfigError::Parse)?;
        Ok(ServerConfig {
            bind: raw.server.bind,
            data_dir: raw.server.data_dir,
        })
    }
}

#[derive(serde::Deserialize)]
struct RawServer {
    server: RawServerInner,
}

#[derive(serde::Deserialize)]
struct RawServerInner {
    bind: String,
    data_dir: String,
}

/// 顶层配置文件（cache + server 两段）。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct AppConfig {
    pub cache: ArcConfig,
    pub server: ServerConfig,
}

impl AppConfig {
    pub fn from_toml_str(text: &str) -> Result<Self, ConfigError> {
        #[derive(serde::Deserialize)]
        struct Raw {
            cache: RawCache,
            server: RawServerInner,
        }
        let raw: Raw = toml::from_str(text).map_err(ConfigError::Parse)?;
        let cache = ArcConfig {
            capacity: raw.cache.capacity,
            page_size: raw.cache.page_size,
            writeback_retries: raw.cache.writeback_retries,
        };
        cache.validate()?;
        Ok(AppConfig {
            cache,
            server: ServerConfig {
                bind: raw.server.bind,
                data_dir: raw.server.data_dir,
            },
        })
    }

    pub fn from_toml_path(path: &Path) -> Result<Self, ConfigError> {
        let text =
            std::fs::read_to_string(path).map_err(|e| ConfigError::Read(path.to_owned(), e))?;
        Self::from_toml_str(&text)
    }
}

#[derive(Debug)]
pub enum ConfigError {
    Parse(toml::de::Error),
    Read(std::path::PathBuf, std::io::Error),
    InvalidField(String),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Parse(e) => write!(f, "invalid config TOML: {e}"),
            ConfigError::Read(p, e) => write!(f, "cannot read config {}: {e}", p.display()),
            ConfigError::InvalidField(msg) => write!(f, "invalid config field: {msg}"),
        }
    }
}

impl std::error::Error for ConfigError {}
