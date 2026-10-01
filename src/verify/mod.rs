//! Standalone validation entry point: independent scalar oracle,
//! differential runner and the built-in boundary case suite.

pub mod cases;
pub mod oracle;
pub mod runner;

pub use oracle::{OVal, OracleFixture, OT};
pub use runner::{
    load_both_paths, run_case, run_suite, Case, CaseFailure, CaseReport, FailureKind, SuiteReport,
};

use std::path::{Path, PathBuf};

/// One fixture's full suite identity.
pub struct SuiteSpec {
    pub name: &'static str,
    pub manifest: PathBuf,
    pub cases: Vec<Case>,
}

/// All built-in suites, relative to the project root given.
pub fn builtin_suites(root: &Path) -> Vec<SuiteSpec> {
    vec![
        SuiteSpec {
            name: "people",
            manifest: root.join("fixtures/people.toml"),
            cases: cases::people_cases(),
        },
        SuiteSpec {
            name: "edge67",
            manifest: root.join("fixtures/edge67.toml"),
            cases: cases::edge67_cases(),
        },
    ]
}

/// Run every built-in suite. Returns the combined report and per-suite names.
pub fn run_builtin(root: &Path) -> Vec<(String, SuiteReport)> {
    builtin_suites(root)
        .into_iter()
        .map(|spec| {
            let fixture_dir = spec
                .manifest
                .parent()
                .expect("manifest has a parent dir")
                .to_path_buf();
            let (store, oracle) = load_both_paths(&spec.manifest, &fixture_dir);
            let report = run_suite(&store, &oracle, &spec.cases);
            (spec.name.to_string(), report)
        })
        .collect()
}
