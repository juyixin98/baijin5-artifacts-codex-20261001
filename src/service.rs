//! Validation entry point: orchestrates rule validation, version-mix rejection,
//! both executors, their partition comparison, and the independent oracle cross-check.
//!
//! This is the real "input -> verdict" pipeline the HTTP API wraps; it has no axum
//! dependency so it can be unit-tested directly.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::batch::StringBatch;
use crate::collation::{rule_for, RepresentativePolicy, RuleVersion};
use crate::diag::{new_request_id, redact, Decision, DiagRecord, FailureCategory};
use crate::exec::{hash_groups, sort_groups, GroupSet};
use crate::oracle::oracle_partition;
use crate::state::AppState;

/// A grouping/dedup request.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct GroupRequest {
    pub column: String,
    /// Rule version tag. Absent -> configured default.
    #[serde(default)]
    pub rule_version: Option<String>,
    /// Optional per-row version tags. If present they must ALL equal `rule_version`,
    /// otherwise mixing is rejected. Demonstrates "different rule versions cannot mix".
    #[serde(default)]
    pub row_rule_versions: Option<Vec<String>>,
    #[serde(default)]
    pub representative_policy: Option<RepresentativePolicy>,
    pub values: Vec<String>,
}

/// How two partitions relate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ComparisonReport {
    pub sort_group_count: usize,
    pub hash_group_count: usize,
    pub oracle_group_count: usize,
    pub partitions_agree: bool,
    pub per_key_hashes_agree: bool,
    pub representatives_agree: bool,
}

/// The full, typed verdict.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Verdict {
    pub request_id: String,
    pub decision: Decision,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failure: Option<FailureCategory>,
    pub rule_version: Option<String>,
    pub comparison: Option<ComparisonReport>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sort_result: Option<GroupSet>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hash_result: Option<GroupSet>,
    /// Dedup view: one representative per class (first-seen order).
    #[serde(default)]
    pub deduplicated: Vec<String>,
    /// Ordered diagnostic trail.
    pub diagnostics: Vec<DiagRecord>,
}

impl Verdict {
    pub fn accepted(&self) -> bool {
        self.decision == Decision::Accepted
    }
    pub fn rejected(&self) -> bool {
        self.decision == Decision::Rejected
    }
}

/// Run the complete compare-and-validate pipeline.
pub fn run_comparison(state: &AppState, req: GroupRequest) -> Verdict {
    run_comparison_with_id(state, req, new_request_id())
}

/// Same as [`run_comparison`] but with a caller-supplied request id (tests/diagnostics).
pub fn run_comparison_with_id(state: &AppState, req: GroupRequest, request_id: String) -> Verdict {
    let mut diags: Vec<DiagRecord> = Vec::new();
    let rid = request_id.as_str();

    // Stage 1: column / input boundary validation.
    if req.column.is_empty() {
        return reject(
            rid,
            FailureCategory::EmptyColumn,
            diags,
            "column name missing",
        );
    }
    if req.values.len() > state.config.max_rows {
        return reject(
            rid,
            FailureCategory::InvalidRow {
                index: req.values.len(),
                reason: format!(
                    "row count {} exceeds max_rows {}",
                    req.values.len(),
                    state.config.max_rows
                ),
            },
            diags,
            "unbounded input guard",
        );
    }
    diags.push(
        DiagRecord::accepted(rid, "input", "column present and row count within bound")
            .with("rows", req.values.len().to_string())
            .with("column_redacted", redact(&req.column)),
    );

    // Stage 2: resolve rule version.
    let version_tag = req
        .rule_version
        .clone()
        .unwrap_or_else(|| state.config.default_rule_version.clone());
    let version = match RuleVersion::parse(&version_tag) {
        Some(v) => v,
        None => {
            return reject(
                rid,
                FailureCategory::UnknownRuleVersion {
                    supplied: version_tag.clone(),
                },
                diags,
                "rule version not in registry; cannot pick an ordering",
            );
        }
    };
    diags.push(
        DiagRecord::accepted(rid, "rule", "resolved rule version")
            .with("rule_version", version.as_str()),
    );

    // Stage 3: reject mixing rule versions across rows in ONE operation.
    if let Some(row_versions) = &req.row_rule_versions {
        if let Err(failure) = validate_no_version_mix(version, row_versions) {
            diags.push(
                DiagRecord::rejected(rid, "version_mix", failure.clone())
                    .with("distinct_versions", distinct_join(row_versions)),
            );
            return Verdict {
                request_id: rid.to_string(),
                decision: Decision::Rejected,
                failure: Some(failure),
                rule_version: Some(version_tag),
                comparison: None,
                sort_result: None,
                hash_result: None,
                deduplicated: Vec::new(),
                diagnostics: diags,
            };
        }
        diags.push(DiagRecord::accepted(
            rid,
            "version_mix",
            "all rows tagged with the same rule version",
        ));
    }

    // Stage 4: build typed batch (fails on structural problems; values themselves are
    // already Rust Strings so this cannot fail here, but the guard is real).
    let batch = match StringBatch::try_new(req.column.clone(), req.values.clone()) {
        Ok(b) => b,
        Err(e) => {
            return reject(
                rid,
                FailureCategory::InvalidRow {
                    index: 0,
                    reason: e.to_string(),
                },
                diags,
                "batch construction failed",
            );
        }
    };

    // Stage 5: both executors.
    let rule = rule_for(version).expect("registry contains resolved version");
    let policy = req
        .representative_policy
        .unwrap_or(state.config.representative_policy);
    let sort_set = sort_groups(rule, &batch, policy);
    let hash_set = hash_groups(rule, &batch, policy);
    diags.push(
        DiagRecord::accepted(
            rid,
            "exec",
            "sort-agg and hash-agg both produced a GroupSet",
        )
        .with("sort_groups", sort_set.group_count().to_string())
        .with("hash_groups", hash_set.group_count().to_string()),
    );

    // Stage 6: compare partitions (row-index equivalence classes).
    let partitions_agree = same_partition(&sort_set, &hash_set);
    let per_key_hashes_agree = same_key_hashes(&sort_set, &hash_set);
    let representatives_agree = same_representatives(&sort_set, &hash_set);

    // Stage 7: independent oracle cross-check (NOT generated by the core).
    let oracle_classes = oracle_partition(version, req.values.as_slice());
    let oracle_count = oracle_classes.len();
    let core_partition = partition_rows(&sort_set);
    let oracle_agrees = core_partition == btreeset_partition(&oracle_classes);

    if state.config.oracle_crosscheck {
        diags.push(
            DiagRecord::accepted(
                rid,
                "oracle",
                if oracle_agrees {
                    "independent pairwise oracle agrees with core partition"
                } else {
                    "independent pairwise oracle DISAGREES with core partition"
                },
            )
            .with("oracle_groups", oracle_count.to_string())
            .with("core_groups", sort_set.group_count().to_string()),
        );
    }

    let report = ComparisonReport {
        sort_group_count: sort_set.group_count(),
        hash_group_count: hash_set.group_count(),
        oracle_group_count: oracle_count,
        partitions_agree,
        per_key_hashes_agree,
        representatives_agree,
    };

    let fully_agreed =
        partitions_agree && per_key_hashes_agree && representatives_agree && oracle_agrees;

    if !fully_agreed {
        let detail = format!(
            "partitions={partitions_agree}, hashes={per_key_hashes_agree}, reps={representatives_agree}, oracle={oracle_agrees}"
        );
        let failure = if !oracle_agrees || !partitions_agree {
            FailureCategory::OracleMismatch {
                detail: detail.clone(),
            }
        } else {
            FailureCategory::ExecutorMismatch {
                detail: detail.clone(),
            }
        };
        diags.push(DiagRecord::rejected(rid, "compare", failure.clone()));
        return Verdict {
            request_id: rid.to_string(),
            decision: Decision::Rejected,
            failure: Some(failure),
            rule_version: Some(version_tag),
            comparison: Some(report),
            sort_result: Some(sort_set),
            hash_result: Some(hash_set),
            deduplicated: Vec::new(),
            diagnostics: diags,
        };
    }

    let deduplicated = first_seen_representatives(&sort_set);
    diags.push(
        DiagRecord::accepted(rid, "compare", "sort-agg == hash-agg == oracle")
            .with("group_count", sort_set.group_count().to_string())
            .with("deduped_rows", deduplicated.len().to_string()),
    );

    Verdict {
        request_id: rid.to_string(),
        decision: Decision::Accepted,
        failure: None,
        rule_version: Some(version_tag),
        comparison: Some(report),
        sort_result: Some(sort_set),
        hash_result: Some(hash_set),
        deduplicated,
        diagnostics: diags,
    }
}

/// A single grouping operation must not combine values carrying different rule
/// versions. `expected` is the operation's declared version; every per-row tag must
/// parse and equal it.
pub fn validate_no_version_mix(
    expected: RuleVersion,
    row_versions: &[String],
) -> Result<(), FailureCategory> {
    // An unparseable tag is an unknown version, not a mix: report it precisely.
    for tag in row_versions {
        if RuleVersion::parse(tag).is_none() {
            return Err(FailureCategory::UnknownRuleVersion {
                supplied: tag.clone(),
            });
        }
    }
    let distinct: BTreeSet<String> = row_versions.iter().cloned().collect();
    let all_expected = row_versions
        .iter()
        .all(|t| RuleVersion::parse(t) == Some(expected));
    if all_expected {
        Ok(())
    } else {
        Err(FailureCategory::RuleVersionMixed {
            versions: distinct.into_iter().collect(),
        })
    }
}

fn reject(rid: &str, failure: FailureCategory, mut diags: Vec<DiagRecord>, why: &str) -> Verdict {
    diags.push(DiagRecord::rejected(rid, "guard", failure.clone()).with("why", why));
    Verdict {
        request_id: rid.to_string(),
        decision: Decision::Rejected,
        failure: Some(failure),
        rule_version: None,
        comparison: None,
        sort_result: None,
        hash_result: None,
        deduplicated: Vec::new(),
        diagnostics: diags,
    }
}

fn distinct_join(values: &[String]) -> String {
    let set: BTreeSet<&str> = values.iter().map(String::as_str).collect();
    set.into_iter().collect::<Vec<_>>().join(",")
}

/// Partition expressed as a set of sorted row-index sets.
fn partition_rows(gs: &GroupSet) -> BTreeSet<BTreeSet<usize>> {
    gs.groups
        .iter()
        .map(|g| g.rows.iter().copied().collect())
        .collect()
}

fn btreeset_partition(classes: &[Vec<usize>]) -> BTreeSet<BTreeSet<usize>> {
    classes
        .iter()
        .map(|c| c.iter().copied().collect())
        .collect()
}

fn same_partition(a: &GroupSet, b: &GroupSet) -> bool {
    partition_rows(a) == partition_rows(b)
}

fn same_key_hashes(a: &GroupSet, b: &GroupSet) -> bool {
    let mut ha: Vec<(String, u64)> = a
        .groups
        .iter()
        .map(|g| (g.key_hex.clone(), g.hash))
        .collect();
    let mut hb: Vec<(String, u64)> = b
        .groups
        .iter()
        .map(|g| (g.key_hex.clone(), g.hash))
        .collect();
    ha.sort();
    hb.sort();
    ha == hb
}

fn same_representatives(a: &GroupSet, b: &GroupSet) -> bool {
    let mut ra: Vec<String> = a.groups.iter().map(|g| g.representative.clone()).collect();
    let mut rb: Vec<String> = b.groups.iter().map(|g| g.representative.clone()).collect();
    ra.sort();
    rb.sort();
    ra == rb
}

fn first_seen_representatives(gs: &GroupSet) -> Vec<String> {
    // sort_result groups are key-ordered; select representative per class then return
    // in order of earliest row so the dedup view reflects input first-seen.
    let mut with_min_row: Vec<(usize, String)> = gs
        .groups
        .iter()
        .map(|g| (*g.rows.first().unwrap_or(&0), g.representative.clone()))
        .collect();
    with_min_row.sort_by_key(|(r, _)| *r);
    with_min_row.into_iter().map(|(_, s)| s).collect()
}
