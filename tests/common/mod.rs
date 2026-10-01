//! Shared test fixtures loader (synthetic, local-only data).
//!
//! Fields are populated by serde reflection and used across separate test binaries,
//! so dead-code/naming lints are relaxed for this fixture module.
#![allow(dead_code, non_snake_case)]

use collate_agg::service::GroupRequest;
use collate_agg::state::{AppConfig, AppState};
use serde::Deserialize;
use std::path::PathBuf;

#[derive(Debug, Deserialize)]
pub struct FixtureRow {
    pub id: usize,
    pub value: String,
}

#[derive(Debug, Deserialize)]
pub struct ExpectedClass {
    pub representative: String,
    pub rows: Vec<usize>,
}

#[derive(Debug, Deserialize)]
pub struct Expected {
    pub group_count: usize,
    pub classes: Vec<ExpectedClass>,
}

#[derive(Debug, Deserialize)]
pub struct Fixture {
    pub column: String,
    pub rule_version: String,
    pub rows: Vec<FixtureRow>,
    pub expected_under_2026R1: Expected,
}

impl Fixture {
    pub fn load() -> Fixture {
        let path: PathBuf =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/strings.json");
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
        serde_json::from_str(&text).expect("fixture parses")
    }

    pub fn values(&self) -> Vec<String> {
        // Fixture rows must be id-ordered and contiguous.
        let mut sorted = self.rows.iter().collect::<Vec<_>>();
        sorted.sort_by_key(|r| r.id);
        sorted.iter().map(|r| r.value.clone()).collect()
    }

    pub fn request(&self) -> GroupRequest {
        GroupRequest {
            column: self.column.clone(),
            rule_version: Some(self.rule_version.clone()),
            row_rule_versions: None,
            representative_policy: None,
            values: self.values(),
        }
    }
}

pub fn test_state() -> AppState {
    AppState::new(AppConfig::default())
}
