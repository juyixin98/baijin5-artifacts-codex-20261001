//! 服务配置：端口、数据目录、调度器默认参数。可被 `config/server.json` 覆盖。

use crate::scheduler::deadline::DeadlineConfig;
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    pub listen_addr: String,
    pub data_dir: String,
    pub fixture_dir: String,
    #[serde(default)]
    pub deadline: DeadlineConfig,
}

impl Default for ServerConfig {
    fn default() -> Self {
        ServerConfig {
            listen_addr: "127.0.0.1:18099".to_string(),
            data_dir: "data".to_string(),
            fixture_dir: "fixtures".to_string(),
            deadline: DeadlineConfig::default(),
        }
    }
}

impl ServerConfig {
    /// 若配置文件存在则加载并覆盖默认值；不存在或解析失败则回退默认（并说明原因）。
    pub fn load(path: &str) -> (Self, Option<String>) {
        match std::fs::read_to_string(Path::new(path)) {
            Ok(text) => match serde_json::from_str::<ServerConfig>(&text) {
                Ok(cfg) => (cfg, None),
                Err(e) => (
                    Self::default(),
                    Some(format!(
                        "config file '{path}' is invalid ({e}); using defaults"
                    )),
                ),
            },
            Err(_) => (Self::default(), None),
        }
    }
}
