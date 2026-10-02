//! Fixture integrity: every compiled-in scenario has a JSON fixture with
//! identical content, so the data files used by the CLI (`--fixture`) and
//! the scenarios used by the API/tests can never drift apart.

mod common;

use cfs_sim::scenario::{builtin_scenarios, Scenario};
use common::log_step;

const CASE: &str = "fixtures";

#[test]
fn fixtures_match_builtin_scenarios() {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures");
    for builtin in builtin_scenarios() {
        let path = format!("{dir}/{}.json", builtin.name);
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("fixture {path} readable: {e}"));
        let loaded = Scenario::from_json(&text)
            .unwrap_or_else(|e| panic!("fixture {path} valid: {e}"));
        log_step(CASE, &format!("{}:matches-builtin", builtin.name),
            "fixture == builtin", &(loaded == builtin).to_string(),
            "fixtures are the data-form of the same scenarios", loaded == builtin);
        assert_eq!(loaded, builtin, "fixture {} drifted", builtin.name);
    }
}

#[test]
fn every_fixture_file_corresponds_to_a_builtin() {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures");
    let builtins: Vec<String> = builtin_scenarios().into_iter().map(|s| s.name).collect();
    let mut files: Vec<String> = std::fs::read_dir(dir)
        .unwrap()
        .filter_map(|e| {
            let name = e.unwrap().file_name().into_string().unwrap();
            name.strip_suffix(".json").map(str::to_string)
        })
        .collect();
    files.sort();
    let mut expected = builtins;
    expected.sort();
    log_step(CASE, "fixture-set", &format!("{expected:?}"), &format!("{files:?}"),
        "no orphan fixtures, no missing fixtures", files == expected);
    assert_eq!(files, expected);
}
