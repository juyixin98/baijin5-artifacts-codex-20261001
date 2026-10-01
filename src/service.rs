//! Service orchestration: validation entry, both executors, equivalence
//! verdict, and the explainable response/trace model.
//!
//! Every operation is correlated to a `request_id` (and optional client
//! `query_id`) in structured logs and in the response body. Failures and
//! uncertain conclusions are always reported as separate sections.

use tracing::{debug, warn};
use uuid::Uuid;

use crate::ast::{
    CatalogReport, ColumnOut, EquivalenceVerdict, ExecutorReport, QueryFailure, QueryRequest,
    QuerySuccess, RewriteProof, StepTrace,
};
use crate::catalog::Catalog;
use crate::config::Config;
use crate::error::EngineError;
use crate::executor;
use crate::validator;

/// Engine revision shown in responses and logs, alongside the crate version.
pub const ENGINE_REVISION: &str = "group-probe-v1";

pub fn engine_version() -> String {
    format!("{}/{}", env!("CARGO_PKG_VERSION"), ENGINE_REVISION)
}

#[derive(Clone)]
pub struct QueryService {
    catalog: Catalog,
    config: Config,
}

impl QueryService {
    pub fn new(catalog: Catalog) -> Self {
        QueryService {
            catalog,
            config: Config::default(),
        }
    }

    pub fn with_config(mut self, config: Config) -> Self {
        self.config = config;
        self
    }

    pub fn catalog(&self) -> &Catalog {
        &self.catalog
    }

    pub fn load_fixtures(
        &self,
        req: &crate::ast::LoadFixturesRequest,
    ) -> crate::error::EngineResult<CatalogReport> {
        let loaded = self
            .catalog
            .load(req, self.config.execution.max_rows_per_relation)?;
        Ok(CatalogReport {
            request_id: Uuid::new_v4().to_string(),
            loaded,
            relations: self.catalog.names(),
        })
    }

    /// Execute one query end to end. Always returns a rendered response (never
    /// an `Err`): failures are first-class results with a stable category.
    pub fn run_query(&self, req: QueryRequest) -> Result<QuerySuccess, Box<QueryFailure>> {
        let request_id = Uuid::new_v4().to_string();
        let query_id = req.query_id.clone();
        let log_ctx = format!(
            "request_id={} query_id={}",
            request_id,
            query_id.as_deref().unwrap_or("-")
        );
        debug!(%log_ctx, op = %req.subquery.op, "query accepted");

        let mut trace: Vec<StepTrace> = Vec::new();
        trace.push(StepTrace {
            step: "receive".to_string(),
            detail: format!(
                "op=`{}` outer={} inner={}",
                req.subquery.op, req.outer.relation, req.subquery.inner_relation
            ),
        });

        // 1. Validation gate (rejects NOT IN and other unsupported forms).
        let plan = match validator::validate(&req, &self.catalog) {
            Ok(plan) => {
                trace.push(StepTrace {
                    step: "validate".to_string(),
                    detail: "request validated; unsupported forms (NOT IN, IN, ANY, ALL) checked"
                        .to_string(),
                });
                plan
            }
            Err(err) => {
                warn!(%log_ctx, category = err.category().code(), error = %err, "query rejected");
                trace.push(StepTrace {
                    step: "validate".to_string(),
                    detail: format!("rejected as {}: {err}", err.category().label()),
                });
                return Err(render_failure(
                    request_id,
                    query_id,
                    err,
                    None,
                    trace,
                    Vec::new(),
                ));
            }
        };

        let proof: RewriteProof = validator::rewrite_proof(&plan);
        trace.push(StepTrace {
            step: "rewrite".to_string(),
            detail: format!(
                "decorrelated via {}; inner grouped once on correlation key(s)",
                ENGINE_REVISION
            ),
        });

        // 2. Prepare typed batches and column indices.
        let prep = match executor::prepare(&plan, &self.catalog) {
            Ok(prep) => prep,
            Err(err) => {
                warn!(%log_ctx, category = err.category().code(), error = %err, "prepare failed");
                return Err(render_failure(
                    request_id,
                    query_id,
                    err,
                    None,
                    trace,
                    vec![
                        "failure arose during plan preparation; no result rows exist.".to_string(),
                    ],
                ));
            }
        };

        // 3. Primary executor: decorrelated group-probe.
        let primary = executor::decorrelated(&prep);
        trace.push(StepTrace {
            step: "execute".to_string(),
            detail: format!(
                "executor=decorrelated location=src/executor.rs::decorrelated: inner scanned once, \
                 {} outer probe(s)",
                prep_probe_count(&prep)
            ),
        });

        // 4. Independent reference interpreter (unless disabled).
        let cross_check_enabled = req
            .cross_check
            .unwrap_or(self.config.execution.cross_check_by_default);
        let reference = if cross_check_enabled {
            let r = executor::row_by_row(&prep);
            trace.push(StepTrace {
                step: "cross_check".to_string(),
                detail: "executor=row_by_row location=src/executor.rs::row_by_row: nested-loop \
                         per-outer-row interpretation"
                    .to_string(),
            });
            Some(r)
        } else {
            None
        };

        // 5. Render outcome, comparing with the independent interpreter.
        match primary {
            Ok(batch) => {
                let columns: Vec<ColumnOut> = batch
                    .columns()
                    .iter()
                    .map(|c| ColumnOut {
                        name: c.name().to_string(),
                        r#type: c.logical_type().name().to_string(),
                    })
                    .collect();
                let rows = batch.to_json_rows();
                debug!(%log_ctx, rows = rows.len(), "query completed");

                let equivalence = reference.as_ref().map(|ref_result| match ref_result {
                    Ok(ref_batch) => {
                        let eq = batches_equal(&batch, ref_batch);
                        EquivalenceVerdict {
                            equivalent: eq,
                            basis:
                                "full row multiset comparison: columns, order, NULL placement and \
                                    duplicate outer rows"
                                    .to_string(),
                            detail: if eq {
                                format!(
                                    "decorrelated and row-by-row returned identical {} row(s)",
                                    rows.len()
                                )
                            } else {
                                "executors disagreed; this is an engine defect — treat result as \
                                 uncertain"
                                    .to_string()
                            },
                        }
                    }
                    Err(ref_err) => EquivalenceVerdict {
                        equivalent: false,
                        basis: "result/failure mismatch".to_string(),
                        detail: format!(
                            "decorrelated succeeded but row-by-row failed with {}: {ref_err}",
                            ref_err.category().code()
                        ),
                    },
                });

                let mut uncertainties = Vec::new();
                if let Some(v) = &equivalence {
                    if !v.equivalent {
                        warn!(%log_ctx, "executor disagreement");
                        uncertainties.push(
                            "the independent interpreter disagreed with the decorrelated executor; \
                             the returned rows must not be trusted."
                                .to_string(),
                        );
                    }
                }
                if !uncertainties.is_empty() {
                    warn!(%log_ctx, ?uncertainties, "uncertain conclusions");
                }

                Ok(QuerySuccess {
                    request_id,
                    query_id,
                    engine_version: engine_version(),
                    columns,
                    rows,
                    primary_executor: "decorrelated".to_string(),
                    executors: executor_reports(batch.row_count(), reference.as_ref()),
                    equivalence,
                    rewrite: proof,
                    trace,
                    uncertainties,
                })
            }
            Err(err) => {
                warn!(%log_ctx, category = err.category().code(), error = %err, "decorrelated executor failed");
                // Does the independent interpreter fail in the same category?
                let failure_cross_check = reference.map(|ref_result| match ref_result {
                    Ok(ref_batch) => {
                        warn!(%log_ctx, ref_rows = ref_batch.row_count(), "cross-check mismatch: reference succeeded");
                        false
                    }
                    Err(ref_err) => {
                        let same = ref_err.category() == err.category();
                        if !same {
                            warn!(
                                %log_ctx,
                                primary = err.category().code(),
                                reference = ref_err.category().code(),
                                "cross-check mismatch: differing failure categories"
                            );
                        }
                        same
                    }
                });

                let mut uncertainties = Vec::new();
                if let Some(false) = failure_cross_check {
                    uncertainties.push(
                        "the independent interpreter did not reproduce this failure category; the \
                         failure diagnosis is uncertain."
                            .to_string(),
                    );
                }

                Err(render_failure(
                    request_id,
                    query_id,
                    err,
                    failure_cross_check,
                    trace,
                    uncertainties,
                ))
            }
        }
    }
}

fn prep_probe_count(prep: &executor::PreparedQuery) -> usize {
    prep.outer_rows()
}

fn batches_equal(a: &crate::batch::RecordBatch, b: &crate::batch::RecordBatch) -> bool {
    if a.row_count() != b.row_count() || a.columns().len() != b.columns().len() {
        return false;
    }
    for (ca, cb) in a.columns().iter().zip(b.columns().iter()) {
        if ca.name() != cb.name() || ca.logical_type() != cb.logical_type() {
            return false;
        }
        for row in 0..a.row_count() {
            if ca.value(row) != cb.value(row) {
                return false;
            }
        }
    }
    true
}

fn executor_reports(
    primary_rows: usize,
    reference: Option<&Result<crate::batch::RecordBatch, EngineError>>,
) -> Vec<ExecutorReport> {
    let mut reports = vec![ExecutorReport {
        mode: "decorrelated".to_string(),
        version: engine_version(),
        location: "src/executor.rs::decorrelated".to_string(),
        row_count: primary_rows,
        failure_category: None,
    }];
    if let Some(r) = reference {
        let (row_count, failure_category) = match r {
            Ok(b) => (b.row_count(), None),
            Err(e) => (0, Some(e.category().code().to_string())),
        };
        reports.push(ExecutorReport {
            mode: "row_by_row".to_string(),
            version: engine_version(),
            location: "src/executor.rs::row_by_row".to_string(),
            row_count,
            failure_category,
        });
    }
    reports
}

fn render_failure(
    request_id: String,
    query_id: Option<String>,
    err: EngineError,
    failure_cross_check: Option<bool>,
    trace: Vec<StepTrace>,
    uncertainties: Vec<String>,
) -> Box<QueryFailure> {
    Box::new(QueryFailure {
        request_id,
        query_id,
        engine_version: engine_version(),
        failure: err.to_failure(),
        failure_cross_check,
        trace,
        uncertainties,
    })
}
