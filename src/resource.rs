//! Resources and state: server limits, the in-memory fixture catalog, request
//! correlation, and redacted diagnostics.
//!
//! Everything here is local and synthetic — no external accounts or business
//! data. Diagnostics deliberately contain only structural metadata (relation
//! names, column names, counts, counters), never row values, so logs stay safe
//! even if inputs were sensitive.
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;

use crate::batch::{RelationSchema, TypedBatch};
use crate::error::{ErrorCategory, ErrorCode, JoinError};
use crate::query::MAX_RELATIONS;

/// Tunable resource limits. Values are validated at startup.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub bind_host: String,
    pub bind_port: u16,
    pub max_relations: usize,
    pub max_rows_per_relation: usize,
    pub max_output_limit: u64,
    pub max_body_bytes: usize,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            bind_host: "127.0.0.1".to_string(),
            bind_port: 8080,
            max_relations: MAX_RELATIONS,
            max_rows_per_relation: 1_000_000,
            max_output_limit: 1_000_000,
            max_body_bytes: 16 * 1024 * 1024,
        }
    }
}

impl ServerConfig {
    pub fn from_env() -> Self {
        let mut cfg = ServerConfig::default();
        if let Ok(v) = std::env::var("LFJ_BIND_HOST") {
            cfg.bind_host = v;
        }
        if let Ok(v) = std::env::var("LFJ_BIND_PORT") {
            if let Ok(port) = v.parse() {
                cfg.bind_port = port;
            }
        }
        cfg
    }
}

/// Why a request was accepted, rejected, or could not be decided.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    /// Passed validation and executed (possibly with a truncated page).
    Accepted,
    /// Hard validation failure; do not retry without changing the request.
    Rejected,
    /// Valid request, but this page could not fully deliver (limit hit).
    /// Resumable via the returned cursor — not a failure.
    Undecidable,
}

/// Redacted, structured diagnostic record. No row values are ever included.
#[derive(Debug, Clone, Serialize)]
pub struct Diagnostics {
    pub request_id: String,
    pub decision: Decision,
    pub error_category: Option<String>,
    pub error_code: Option<String>,
    pub reason: Option<String>,
    pub relation_count: usize,
    pub relation_names: Vec<String>,
    pub join_variables: Vec<String>,
    pub output_attributes: Vec<String>,
    pub null_policy: String,
    pub input_rows: Vec<usize>,
    pub distinct_rows: Vec<usize>,
    pub null_join_rows_dropped: Vec<usize>,
    pub counters: Option<crate::trie::Counters>,
    pub page_rows: usize,
    pub truncated: bool,
    pub resumable: bool,
    pub redaction: &'static str,
}

impl Diagnostics {
    const REDACTION_NOTE: &'static str =
        "row values are never logged; structural metadata and counters only";

    pub fn redaction_note() -> &'static str {
        Self::REDACTION_NOTE
    }

    pub fn rejected(request_id: &str, err: &JoinError, relation_names: Vec<String>) -> Self {
        Self {
            request_id: request_id.to_string(),
            decision: Decision::Rejected,
            error_category: Some(err.category().to_string()),
            error_code: Some(err.code.as_str().to_string()),
            reason: Some(err.message.clone()),
            relation_count: relation_names.len(),
            relation_names,
            join_variables: Vec::new(),
            output_attributes: Vec::new(),
            null_policy: String::new(),
            input_rows: Vec::new(),
            distinct_rows: Vec::new(),
            null_join_rows_dropped: Vec::new(),
            counters: None,
            page_rows: 0,
            truncated: false,
            resumable: false,
            redaction: Self::REDACTION_NOTE,
        }
    }

    /// Map an error category to the tri-state decision. Resource exhaustion is
    /// "undecidable" (retry/resume); malformed input is "rejected".
    pub fn decision_for(err: &JoinError) -> Decision {
        match err.category() {
            ErrorCategory::ResourceLimit => Decision::Undecidable,
            _ => Decision::Rejected,
        }
    }
}

/// Immutable named fixture batches available to all requests.
#[derive(Debug, Default)]
pub struct Catalog {
    relations: Vec<(String, Arc<RelationSchema>, TypedBatch)>,
}

impl Catalog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(
        &mut self,
        name: impl Into<String>,
        schema: Arc<RelationSchema>,
        batch: TypedBatch,
    ) {
        self.relations.push((name.into(), schema, batch));
    }

    pub fn entries(&self) -> &[(String, Arc<RelationSchema>, TypedBatch)] {
        &self.relations
    }

    pub fn len(&self) -> usize {
        self.relations.len()
    }

    pub fn is_empty(&self) -> bool {
        self.relations.is_empty()
    }
}

/// Shared application state handed to Axum.
#[derive(Clone)]
pub struct AppState {
    pub config: Arc<ServerConfig>,
    pub catalog: Arc<RwLock<Catalog>>,
}

impl AppState {
    pub fn new(config: ServerConfig, catalog: Catalog) -> Self {
        Self {
            config: Arc::new(config),
            catalog: Arc::new(RwLock::new(catalog)),
        }
    }

    /// Enforce row-count resource limits on inline input.
    pub fn check_resource_limits(
        &self,
        relation_rows: &[(String, usize)],
    ) -> Result<(), JoinError> {
        if relation_rows.len() > self.config.max_relations {
            return Err(JoinError::new(
                ErrorCode::TooManyRelations,
                format!(
                    "at most {} relations allowed, got {}",
                    self.config.max_relations,
                    relation_rows.len()
                ),
            ));
        }
        for (name, rows) in relation_rows {
            if *rows > self.config.max_rows_per_relation {
                return Err(JoinError::new(
                    ErrorCode::BadLimit,
                    format!(
                        "relation '{name}' has {rows} rows, exceeding limit {}",
                        self.config.max_rows_per_relation
                    ),
                ));
            }
        }
        Ok(())
    }
}

/// Generate or echo a correlation id.
pub fn resolve_request_id(given: Option<String>) -> String {
    given
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string())
}
