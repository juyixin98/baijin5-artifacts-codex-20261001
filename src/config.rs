//! Service configuration: device model parameters, scheduler parameters,
//! engine defaults, and storage locations. Loaded from JSON; missing file or
//! missing fields fall back to the defaults below.

use crate::model::DeviceModel;
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub hdd: HddParams,
    pub ssd_like: SsdLikeParams,
    pub deadline: DeadlineParams,
    pub scan_initial_up: bool,
    pub head_start_sector: u64,
    pub fixtures_dir: String,
    pub data_dir: String,
    pub bind_addr: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct HddParams {
    pub seek_base_ns: u64,
    pub seek_ns_per_sector: u64,
    pub transfer_ns_per_sector: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct SsdLikeParams {
    pub fixed_latency_ns: u64,
    pub transfer_ns_per_sector: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct DeadlineParams {
    pub read_expire_ns: u64,
    pub write_expire_ns: u64,
    pub writes_starved: u32,
}

impl Default for HddParams {
    fn default() -> Self {
        HddParams {
            seek_base_ns: 2_000,
            seek_ns_per_sector: 4,
            transfer_ns_per_sector: 8,
        }
    }
}

impl Default for SsdLikeParams {
    fn default() -> Self {
        SsdLikeParams {
            fixed_latency_ns: 5_000,
            transfer_ns_per_sector: 2,
        }
    }
}

impl Default for DeadlineParams {
    fn default() -> Self {
        DeadlineParams {
            read_expire_ns: 500_000,
            write_expire_ns: 5_000_000,
            writes_starved: 2,
        }
    }
}

impl Default for Config {
    fn default() -> Self {
        Config {
            hdd: HddParams::default(),
            ssd_like: SsdLikeParams::default(),
            deadline: DeadlineParams::default(),
            scan_initial_up: true,
            head_start_sector: 0,
            fixtures_dir: "fixtures/traces".to_string(),
            data_dir: "data".to_string(),
            bind_addr: "127.0.0.1:8080".to_string(),
        }
    }
}

impl Config {
    pub fn load(path: &Path) -> Config {
        match std::fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text).unwrap_or_else(|e| {
                eprintln!("config {} malformed ({e}); using defaults", path.display());
                Config::default()
            }),
            Err(_) => Config::default(),
        }
    }

    pub fn default_device(&self, kind: &str) -> Option<DeviceModel> {
        match kind {
            "hdd" => Some(DeviceModel::Hdd {
                seek_base_ns: self.hdd.seek_base_ns,
                seek_ns_per_sector: self.hdd.seek_ns_per_sector,
                transfer_ns_per_sector: self.hdd.transfer_ns_per_sector,
            }),
            "ssd_like" => Some(DeviceModel::SsdLike {
                fixed_latency_ns: self.ssd_like.fixed_latency_ns,
                transfer_ns_per_sector: self.ssd_like.transfer_ns_per_sector,
            }),
            _ => None,
        }
    }
}
