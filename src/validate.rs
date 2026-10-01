//! Verification entry point: the comparison contract.
//!
//! Given a rule version and typed rows it runs **both** executors, asserts the
//! two strategies agree, compares against independently authored expectations,
//! folds into a version-pinned stateful session, and emits a categorized
//! verdict plus a diagnostic record.
//!
//! Verdict categories are deliberately finer than `Ok/Err`: a request can be
//! structurally valid yet **undetermined** (numeric token saturated, or the
//! two executors disagree). HTTP, CLI and tests share this one path.

use serde::{Deserialize, Serialize};

use crate::batch::{InputBatch, Row};
use crate::collation::{Collator, Rule};
use crate::error::Result;
use crate::operators::{self, ExecOutput};
use crate::state::{redact, AppState, DiagRecord, StateError};

/// One input row on the wire.
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
pub struct ApiRow {
    pub record_id: String,
    #[serde(default)]
    pub value: Option<String>,
}

/// Verification request (the JSON body of `POST /verify`).
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
pub struct VerifyRequest {
    /// Caller-supplied correlation id; generated if absent/empty.
    #[serde(default)]
    pub request_id: Option<String>,
    /// Collation rule version (1 = accent/case/numeric, 2 = binary).
    pub rule_version: u16,
    pub rows: Vec<ApiRow>,
    /// Stateful session id. Once created it is pinned to `rule_version`.
    #[serde(default)]
    pub session_id: Option<String>,
    /// Independently authored expected number of groups (contract check).
    #[serde(default)]
    pub expect_group_count: Option<usize>,
    /// Independently authored expected distinct representatives, in sorted
    /// key order. Compared verbatim against retained representative values.
    #[serde(default)]
    pub expect_distinct: Option<Vec<String>>,
    /// When true, response samples and logs carry only length+fingerprint.
    #[serde(default)]
    pub sensitive: bool,
}

/// Failure/accept class. [`Category::as_str`] is the stable machine code.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Category {
    Accepted,
    RejectUnknownRule,
    RejectEmptyRecordId,
    RejectDuplicateRecordId,
    RejectTooManyRows,
    RejectValueTooLarge,
    RejectRuleVersionMismatch,
    RejectExpectedCount,
    RejectExpectedDistinct,
    UndeterminedNumericOverflow,
    UndeterminedExecutorMismatch,
}

impl Category {
    pub fn as_str(self) -> &'static str {
        match self {
            Category::Accepted => "accepted",
            Category::RejectUnknownRule => "reject_unknown_rule",
            Category::RejectEmptyRecordId => "reject_empty_record_id",
            Category::RejectDuplicateRecordId => "reject_duplicate_record_id",
            Category::RejectTooManyRows => "reject_too_many_rows",
            Category::RejectValueTooLarge => "reject_value_too_large",
            Category::RejectRuleVersionMismatch => "reject_rule_version_mismatch",
            Category::RejectExpectedCount => "reject_expected_count",
            Category::RejectExpectedDistinct => "reject_expected_distinct",
            Category::UndeterminedNumericOverflow => "undetermined_numeric_overflow",
            Category::UndeterminedExecutorMismatch => "undetermined_executor_mismatch",
        }
    }

    /// Top-level triage used by every renderer.
    pub fn status(self) -> &'static str {
        match self {
            Category::Accepted => "accepted",
            Category::UndeterminedNumericOverflow | Category::UndeterminedExecutorMismatch => {
                "undetermined"
            }
            _ => "rejected",
        }
    }

    pub fn is_accepted(self) -> bool {
        self == Category::Accepted
    }
}

/// One group as rendered in the report.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct GroupDto {
    pub representative: String,
    pub representative_record_id: String,
    pub member_record_ids: Vec<String>,
    pub count: usize,
    pub is_null: bool,
}

impl GroupDto {
    fn from_group(g: &operators::Group, sensitive: bool) -> Self {
        GroupDto {
            representative: if g.is_null {
                String::new()
            } else {
                redact(Some(&g.representative), sensitive)
            },
            representative_record_id: g.representative_record_id.clone(),
            member_record_ids: g.member_record_ids.clone(),
            count: g.count,
            is_null: g.is_null,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct SessionView {
    pub batches: usize,
    pub total_rows: usize,
    pub rule_version: u16,
}

/// The verdict returned to every caller.
#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub request_id: String,
    pub session_id: Option<String>,
    pub rule_version: u16,
    pub rule_name: String,
    pub status: &'static str,
    pub category: Category,
    pub detail: String,
    pub rows: usize,
    pub group_count: Option<usize>,
    pub distinct_values: Option<Vec<String>>,
    pub sort_groups: Option<Vec<GroupDto>>,
    pub hash_groups: Option<Vec<GroupDto>>,
    pub overflow_record_ids: Vec<String>,
    pub session: Option<SessionView>,
}

/// Run the full contract. Rejection/undetermined are normal verdicts, not
/// `Err`; `Err` is reserved for operator faults (Arrow/lock poisoning).
pub fn verify(state: &AppState, req: VerifyRequest) -> Result<Report> {
    let request_id = req
        .request_id
        .clone()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| state.new_request_id());

    let rule = match Rule::from_version(req.rule_version) {
        Some(r) => r,
        None => {
            return reject(
                state,
                &request_id,
                &req,
                Category::RejectUnknownRule,
                format!(
                    "rule version {} is not registered; known versions: [1, 2]",
                    req.rule_version
                ),
            );
        }
    };

    if let Some(category) = validate_shape(state, &req) {
        return reject(
            state,
            &request_id,
            &req,
            category,
            shape_detail(category, &req),
        );
    }

    let rows = to_rows(&req);
    let batch = InputBatch::from_rows(&rows)?;
    let collator = Collator::new(rule);

    let sort_out = operators::sort_group(&batch, &collator)?;
    let hash_out = operators::hash_group(&batch, &collator)?;

    finish(state, request_id, req, rule, sort_out, hash_out)
}

fn validate_shape(state: &AppState, req: &VerifyRequest) -> Option<Category> {
    if req.rows.len() > state.settings.max_rows {
        return Some(Category::RejectTooManyRows);
    }
    let mut seen = std::collections::HashSet::with_capacity(req.rows.len());
    for r in &req.rows {
        if r.record_id.is_empty() {
            return Some(Category::RejectEmptyRecordId);
        }
        if !seen.insert(&r.record_id) {
            return Some(Category::RejectDuplicateRecordId);
        }
        if let Some(v) = &r.value {
            if v.len() > state.settings.max_value_bytes {
                return Some(Category::RejectValueTooLarge);
            }
        }
    }
    None
}

fn shape_detail(c: Category, req: &VerifyRequest) -> String {
    match c {
        Category::RejectTooManyRows => {
            format!("{} rows exceeds the configured limit", req.rows.len())
        }
        Category::RejectValueTooLarge => "a value exceeds the byte length limit".into(),
        Category::RejectEmptyRecordId => "every row requires a non-empty record_id".into(),
        Category::RejectDuplicateRecordId => {
            "record_id values must be unique within a request".into()
        }
        _ => c.as_str().into(),
    }
}

fn to_rows(req: &VerifyRequest) -> Vec<Row> {
    req.rows
        .iter()
        .map(|r| Row {
            record_id: r.record_id.clone(),
            value: r.value.clone(),
        })
        .collect()
}

/// Compare strategies, check expectations, fold the session, log, render.
fn finish(
    state: &AppState,
    request_id: String,
    req: VerifyRequest,
    rule: Rule,
    sort_out: ExecOutput,
    hash_out: ExecOutput,
) -> Result<Report> {
    let rows = req.rows.len();
    let mut category = Category::Accepted;
    let mut notes: Vec<String> = Vec::new();

    // Contract: sort and hash must produce identical partitions.
    if sort_out != hash_out {
        category = Category::UndeterminedExecutorMismatch;
        notes.push(format!(
            "sort produced {} groups, hash produced {} groups",
            sort_out.group_count(),
            hash_out.group_count()
        ));
    }

    let overflow = sort_out.warnings.numeric_overflow.clone();
    if !overflow.is_empty() && category == Category::Accepted {
        category = Category::UndeterminedNumericOverflow;
        notes.push(format!(
            "{} record(s) contained a digit run wider than u64; distinct values may have collapsed",
            overflow.len()
        ));
    }

    // Independently authored expectations are checked but never overwrite a
    // stronger undetermined verdict.
    if let Some(expected) = req.expect_group_count {
        if expected != sort_out.group_count() && category == Category::Accepted {
            category = Category::RejectExpectedCount;
            notes.push(format!(
                "independently expected {expected} groups, computed {}",
                sort_out.group_count()
            ));
        }
    }
    if let Some(expected) = &req.expect_distinct {
        let got = sort_out.distinct_values();
        if expected != &got && category == Category::Accepted {
            category = Category::RejectExpectedDistinct;
            notes.push(format!(
                "independently expected distinct {expected:?}, computed {got:?}"
            ));
        }
    }

    // Stateful session: a version mismatch rejects and must NOT fold the batch.
    let mut session_view = None;
    if let Some(sid) = &req.session_id {
        match state.sessions.add_batch(sid, rule.version, rows) {
            Ok(s) => {
                let _ = state.sessions.merge(sid, rule.version, &sort_out)?;
                session_view = Some(SessionView {
                    batches: s.batches,
                    total_rows: s.rows,
                    rule_version: s.rule_version,
                });
            }
            Err(StateError::RuleVersionMismatch { existing, incoming }) => {
                category = Category::RejectRuleVersionMismatch;
                notes.push(format!(
                    "session {sid} is pinned to rule v{existing}; refusing a v{incoming} batch"
                ));
            }
            Err(StateError::UnknownSession(_)) => {
                // add_batch always creates; reaching here is an operator fault.
                return Err(crate::error::AppError::Api("session lookup failed".into()));
            }
        }
    }

    let status = category.status();
    let detail = if notes.is_empty() {
        format!(
            "{status}: sort and hash agree on {} equivalence class(es) under {}",
            sort_out.group_count(),
            rule.describe()
        )
    } else {
        notes.join("; ")
    };

    log_diag(
        state,
        &request_id,
        &req,
        category,
        &detail,
        rows,
        Some(sort_out.group_count()),
    );

    Ok(Report {
        request_id,
        session_id: req.session_id.clone(),
        rule_version: rule.version,
        rule_name: rule.name.into(),
        status,
        category,
        detail,
        rows,
        group_count: Some(sort_out.group_count()),
        distinct_values: Some(render_distinct(&sort_out, req.sensitive)),
        sort_groups: Some(
            sort_out
                .groups
                .iter()
                .map(|g| GroupDto::from_group(g, req.sensitive))
                .collect(),
        ),
        hash_groups: Some(
            hash_out
                .groups
                .iter()
                .map(|g| GroupDto::from_group(g, req.sensitive))
                .collect(),
        ),
        overflow_record_ids: overflow,
        session: session_view,
    })
}

fn render_distinct(out: &ExecOutput, sensitive: bool) -> Vec<String> {
    out.distinct_values()
        .into_iter()
        .map(|v| redact(Some(&v), sensitive))
        .collect()
}

/// Build, log and return a pre-execution rejection report.
fn reject(
    state: &AppState,
    request_id: &str,
    req: &VerifyRequest,
    category: Category,
    detail: String,
) -> Result<Report> {
    let status = category.status();
    log_diag(
        state,
        request_id,
        req,
        category,
        &detail,
        req.rows.len(),
        None,
    );

    let (rv, rn) = match Rule::from_version(req.rule_version) {
        Some(r) => (r.version, r.name.to_string()),
        None => (req.rule_version, format!("unknown(v{})", req.rule_version)),
    };
    Ok(Report {
        request_id: request_id.to_string(),
        session_id: req.session_id.clone(),
        rule_version: rv,
        rule_name: rn,
        status,
        category,
        detail,
        rows: req.rows.len(),
        group_count: None,
        distinct_values: None,
        sort_groups: None,
        hash_groups: None,
        overflow_record_ids: vec![],
        session: None,
    })
}

fn log_diag(
    state: &AppState,
    request_id: &str,
    req: &VerifyRequest,
    category: Category,
    detail: &str,
    rows: usize,
    groups: Option<usize>,
) {
    // Diagnostics never embed raw values; membership/identity ids and counts
    // only. Values reach the log solely through the redaction helper.
    state.diag.push(DiagRecord {
        request_id: request_id.to_string(),
        session_id: req.session_id.clone(),
        rule_version: Some(req.rule_version),
        status: category.status(),
        reason_code: category.as_str().to_string(),
        detail: detail.to_string(),
        rows,
        groups,
    });
}
