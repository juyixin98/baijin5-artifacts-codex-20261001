//! HTTP request DTOs. Kept separate from engine types so the wire contract can
//! evolve independently and every string is parsed at the boundary into a
//! typed enum/error.

use serde::Deserialize;

use crate::operator::{ExecutionMode, Qualifier};
use crate::resource::ResourceLimits;

#[derive(Debug, Deserialize)]
pub struct QueryRequest {
    /// `UNION` | `INTERSECT` | `EXCEPT` (case-insensitive).
    pub op: String,
    /// `distinct` (default) or `all`.
    #[serde(default)]
    pub qualifier: Option<String>,
    /// `auto` (default), `in_memory`, `external`.
    #[serde(default)]
    pub mode: Option<String>,
    /// Typed header, e.g. `id:bigint,label:text`.
    pub schema: String,
    pub left: Source,
    pub right: Source,
    #[serde(default)]
    pub run_id: Option<String>,
    /// Arrow2 rows per input/output batch (fixture chunk size when a source
    /// does not override it).
    #[serde(default)]
    pub batch_rows: Option<usize>,
    #[serde(default)]
    pub limits: Option<LimitsDto>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum Source {
    Fixture {
        fixture: String,
        #[serde(default)]
        batch_rows: Option<usize>,
    },
    Rows {
        rows: Vec<Vec<serde_json::Value>>,
        #[serde(default)]
        batch_rows: Option<usize>,
    },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
#[derive(Default)]
pub struct LimitsDto {
    pub memory_bytes: Option<usize>,
    pub allow_spill: Option<bool>,
    pub partition_fanout: Option<usize>,
    pub max_partition_depth: Option<usize>,
    pub max_count: Option<u64>,
    pub max_output_rows: Option<u64>,
    pub spill_bytes: Option<u64>,
}

impl QueryRequest {
    pub fn qualifier(&self) -> Qualifier {
        match self.qualifier.as_deref() {
            Some("all") | Some("ALL") | Some("All") => Qualifier::All,
            _ => Qualifier::Distinct,
        }
    }

    pub fn mode(&self) -> ExecutionMode {
        match self
            .mode
            .as_deref()
            .map(|s| s.to_ascii_lowercase())
            .as_deref()
        {
            Some("in_memory") | Some("inmemory") => ExecutionMode::InMemory,
            Some("external") => ExecutionMode::External,
            _ => ExecutionMode::Auto,
        }
    }

    pub fn limits(&self) -> ResourceLimits {
        let mut out = ResourceLimits::default();
        if let Some(l) = &self.limits {
            if let Some(v) = l.memory_bytes {
                out.memory_bytes = v;
            }
            if let Some(v) = l.allow_spill {
                out.allow_spill = v;
            }
            if let Some(v) = l.partition_fanout {
                out.partition_fanout = v;
            }
            if let Some(v) = l.max_partition_depth {
                out.max_partition_depth = v;
            }
            if let Some(v) = l.max_count {
                out.max_count = v;
            }
            if let Some(v) = l.max_output_rows {
                out.max_output_rows = v;
            }
            if let Some(v) = l.spill_bytes {
                out.spill_bytes = v;
            }
        }
        out
    }
}
