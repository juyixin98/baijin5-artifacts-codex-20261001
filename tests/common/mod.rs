//! Shared test utilities.
//!
//! [`reference`] is an INDEPENDENT naive implementation of the same SQL
//! semantics: plain `HashMap` grouping plus standard-library sorts. It never
//! calls any function from the `pctl` crate, so it can serve as a cross-check
//! that the production external-sort engine did not grade its own homework.

#![allow(dead_code)]

use std::collections::BTreeMap;

use pctl::config::Config;
use pctl::diagnostics::RequestId;
use pctl::exec::execute_request;
use pctl::exec::ExecOutcome;
use pctl::resources::CancellationToken;
use serde_json::{json, Value};
use tempfile::TempDir;

/// Build a server config whose spill directory is a fresh temp dir.
pub fn test_config() -> (Config, TempDir) {
    let dir = TempDir::new().expect("tempdir");
    let cfg = Config {
        bind_addr: "127.0.0.1:0".into(),
        memory_budget_bytes: 64 * 1024 * 1024,
        spill_dir: dir.path().join("spill"),
        max_spill_bytes: 256 * 1024 * 1024,
        group_table_cap: 100_000,
    };
    cfg.ensure_spill_dir().unwrap();
    (cfg, dir)
}

/// Run a JSON request to completion and return the response body as JSON.
pub fn run_to_json(req: Value, config: &Config) -> Value {
    let parsed: pctl::spec::QueryRequest = serde_json::from_value(req).unwrap();
    let rid = RequestId::new();
    match execute_request(&parsed, &rid, config, CancellationToken::new()).unwrap() {
        ExecOutcome::Complete(groups, _stats) => {
            let grp: Vec<Value> = groups
                .into_iter()
                .map(|g| serde_json::to_value(g).unwrap())
                .collect();
            json!({ "status": "complete", "groups": grp })
        }
        other => panic!("expected complete outcome, got {other:?}"),
    }
}

/// Run and also return the raw outcome (for cancellation tests).
pub fn run_raw(req: Value, config: &Config) -> ExecOutcome {
    let parsed: pctl::spec::QueryRequest = serde_json::from_value(req).unwrap();
    let rid = RequestId::new();
    execute_request(&parsed, &rid, config, CancellationToken::new()).unwrap()
}

/// Find a group object in a complete response.
pub fn find_group(resp: &Value, group: Value) -> &Value {
    resp["groups"]
        .as_array()
        .unwrap()
        .iter()
        .find(|g| g["group"] == group)
        .unwrap_or_else(|| panic!("group {group} not found"))
}

/// Result value for operator index `op` within a group.
pub fn result_value(group: &Value, op: usize) -> Value {
    group["results"][op]["value"].clone()
}

pub fn result_status(group: &Value, op: usize) -> &str {
    group["results"][op]["status"].as_str().unwrap()
}

// ---------------------------------------------------------------------------
// Independent reference implementation
// ---------------------------------------------------------------------------

pub mod reference {
    use super::*;

    /// Naive grouped evaluation of a request, expressed purely with
    /// `BTreeMap`/`Vec` and std sorts. Returns groups keyed by their JSON group
    /// value (null group keyed by JSON null), each with per-operator results.
    pub fn evaluate(req: &Value) -> BTreeMap<String, Value> {
        let group_by = req["group_by"].as_str().unwrap();
        let n = req["columns"][0]["values"].as_array().unwrap().len();
        let col_idx = |name: &str| {
            req["columns"]
                .as_array()
                .unwrap()
                .iter()
                .position(|c| c["name"] == name)
                .unwrap()
        };

        // groups in first-seen order is irrelevant; BTreeMap sorts by encoded key
        let mut groups: BTreeMap<String, Vec<usize>> = BTreeMap::new();
        for row in 0..n {
            let key = &req["columns"][col_idx(group_by)]["values"][row];
            groups.entry(key.to_string()).or_default().push(row);
        }

        let mut out = BTreeMap::new();
        for (key, rows) in groups {
            let mut ops = Vec::new();
            for op in req["operators"].as_array().unwrap() {
                ops.push(eval_op(req, op, &rows));
            }
            let key_val: Value = serde_json::from_str(&key).unwrap();
            out.insert(
                key,
                json!({ "group": key_val, "rows": rows.len(), "results": ops }),
            );
        }
        out
    }

    fn col_values(req: &Value, rows: &[usize], col: &str) -> (String, Vec<Value>) {
        let ci = req["columns"]
            .as_array()
            .unwrap()
            .iter()
            .position(|c| c["name"] == col)
            .unwrap();
        let dt = req["columns"][ci]["data_type"]
            .as_str()
            .unwrap()
            .to_string();
        let vals = rows
            .iter()
            .filter_map(|&r| {
                let v = &req["columns"][ci]["values"][r];
                if v.is_null() {
                    None
                } else {
                    assert_eq_dt(v, &dt);
                    Some(v.clone())
                }
            })
            .collect();
        (dt, vals)
    }

    fn assert_eq_dt(v: &Value, dt: &str) {
        match dt {
            "i64" => assert!(v.is_i64(), "expected i64, got {v}"),
            "f64" => assert!(v.is_number(), "expected f64, got {v}"),
            "utf8" => assert!(v.is_string(), "expected utf8, got {v}"),
            other => panic!("unknown type {other}"),
        }
    }

    fn eval_op(req: &Value, op: &Value, rows: &[usize]) -> Value {
        let col = op["column"].as_str().unwrap();
        let (dt, vals) = col_values(req, rows, col);
        match op["op"].as_str().unwrap() {
            "percentile" => {
                let p = op["p"].as_f64().unwrap();
                match op["method"].as_str().unwrap() {
                    "continuous" => {
                        if vals.is_empty() {
                            return ok_null(vals.len());
                        }
                        let mut nums: Vec<f64> = vals.iter().map(|v| v.as_f64().unwrap()).collect();
                        nums.sort_by(|a, b| a.partial_cmp(b).unwrap());
                        if nums.iter().any(|v| v.is_nan()) {
                            return json!({"status": "indeterminate", "code": "nan_measure"});
                        }
                        let h = (nums.len() as f64 - 1.0) * p;
                        let lo = h.floor() as usize;
                        let hi = (lo + 1).min(nums.len() - 1);
                        let frac = h - h.floor();
                        let q = nums[lo] + frac * (nums[hi] - nums[lo]);
                        json!({"status":"ok","type":"f64","value":q,"non_null":vals.len()})
                    }
                    "discrete" => {
                        if vals.is_empty() {
                            return ok_null(vals.len());
                        }
                        let rank = (p * vals.len() as f64).ceil().max(1.0) as usize;
                        let idx = rank - 1;
                        match dt.as_str() {
                            "i64" => {
                                let mut v: Vec<i64> =
                                    vals.iter().map(|x| x.as_i64().unwrap()).collect();
                                v.sort();
                                json!({"status":"ok","type":"i64","value":v[idx],
                                       "non_null":vals.len()})
                            }
                            "f64" => {
                                let mut v: Vec<f64> =
                                    vals.iter().map(|x| x.as_f64().unwrap()).collect();
                                v.sort_by(|a, b| a.partial_cmp(b).unwrap());
                                json!({"status":"ok","type":"f64","value":v[idx],
                                       "non_null":vals.len()})
                            }
                            "utf8" => {
                                let mut v: Vec<&str> =
                                    vals.iter().map(|x| x.as_str().unwrap()).collect();
                                v.sort();
                                json!({"status":"ok","type":"utf8","value":v[idx],
                                       "non_null":vals.len()})
                            }
                            other => panic!("bad type {other}"),
                        }
                    }
                    other => panic!("bad method {other}"),
                }
            }
            "mode" => {
                if vals.is_empty() {
                    return ok_null(0);
                }
                // Independently order by *typed* values (not JSON text), then
                // take the longest equal run; ties resolve to the smallest.
                let (typ, winner, tie, best): (&str, Value, bool, u64) = match dt.as_str() {
                    "i64" => {
                        let mut v: Vec<i64> = vals.iter().map(|x| x.as_i64().unwrap()).collect();
                        v.sort_unstable();
                        let (w, t, f) = mode_of_sorted(&v);
                        ("i64", json!(w), t, f)
                    }
                    "f64" => {
                        let mut v: Vec<f64> = vals.iter().map(|x| x.as_f64().unwrap()).collect();
                        v.sort_by(|a, b| a.total_cmp(b));
                        let (w, t, f) = mode_of_sorted(&v);
                        ("f64", serde_json::Number::from_f64(w).unwrap().into(), t, f)
                    }
                    "utf8" => {
                        let mut v: Vec<&str> = vals.iter().map(|x| x.as_str().unwrap()).collect();
                        v.sort_unstable();
                        let (w, t, f) = mode_of_sorted(&v);
                        ("utf8", json!(w), t, f)
                    }
                    other => panic!("bad type {other}"),
                };
                json!({"status":"ok","type":typ,"value":winner,"tie":tie,
                       "frequency":best,"non_null":vals.len()})
            }
            "string_agg" => {
                if vals.is_empty() {
                    return ok_null(0);
                }
                let mut v: Vec<&str> = vals.iter().map(|x| x.as_str().unwrap()).collect();
                v.sort();
                if op["order"].as_str() == Some("desc") {
                    v.reverse();
                }
                let delim = op["delimiter"].as_str().unwrap_or(",");
                let joined = v.join(delim);
                json!({"status":"ok","type":"utf8","value":joined,"non_null":vals.len()})
            }
            other => panic!("unknown op {other}"),
        }
    }

    fn ok_null(non_null: usize) -> Value {
        json!({"status":"ok","type":"null","value":Value::Null,"non_null":non_null})
    }

    /// Returns (winner, tie, frequency) over a slice sorted ascending. Equal
    /// keys are contiguous, so one linear pass suffices.
    fn mode_of_sorted<T: Clone + PartialEq>(sorted: &[T]) -> (T, bool, u64) {
        assert!(!sorted.is_empty());
        let mut best_val = sorted[0].clone();
        let mut best_freq = 0u64;
        let mut tie = false;
        let mut i = 0usize;
        while i < sorted.len() {
            let mut j = i + 1;
            while j < sorted.len() && sorted[j] == sorted[i] {
                j += 1;
            }
            let freq = (j - i) as u64;
            if freq > best_freq {
                best_freq = freq;
                best_val = sorted[i].clone();
                tie = false;
            } else if freq == best_freq {
                tie = true; // earlier, smaller key retained
            }
            i = j;
        }
        (best_val, tie, best_freq)
    }
}

// ---------------------------------------------------------------------------
// Deterministic pseudo-random fixture generator (no external rand crate)
// ---------------------------------------------------------------------------

pub struct Lcg(pub u64);

impl Lcg {
    pub fn next_u64(&mut self) -> u64 {
        // MMIX constants
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        self.0
    }
    pub fn below(&mut self, m: u64) -> u64 {
        self.next_u64() % m
    }
}
