//! Execution orchestration: validate → prepare → run/page → trace →
//! render. The engine owns run ids, traces, fingerprints and cursor
//! sessions, and is the only place native row positions are translated
//! back to caller row identities / arrow2.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::Arc;

use arrow2::array::{Array, Int64Array, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};

use crate::batch::Batch;
use crate::dto::{CountersDto, CursorPageResponse, PairDto, ParsedJoin};
use crate::error::{JoinError, JoinResult};
use crate::operator::bitmap::Counters;
use crate::operator::iejoin::{JoinRunner, MatchPair, PreparedJoin};
use crate::resource::Budget;
use crate::state::{ensure_open, CursorSession, ExecutionState, SessionStore};
use crate::trace::{ErrorSnapshot, Outcome, RunRecord, Tracer};

/// arrow2 schema of an emitted pair stream: identities plus stable
/// within-run positions.
#[must_use]
pub fn pairs_schema() -> Arc<Schema> {
    Arc::new(Schema::from(vec![
        Field::new("left_id", DataType::Utf8, false),
        Field::new("right_id", DataType::Utf8, false),
        Field::new("left_row", DataType::Int64, false),
        Field::new("right_row", DataType::Int64, false),
    ]))
}

/// Render positional pairs as identities + one arrow2 [`Chunk`].
#[must_use]
pub fn render_page(
    left: &Batch,
    right: &Batch,
    pairs: &[MatchPair],
) -> (Vec<PairDto>, Chunk<Box<dyn Array>>) {
    let mut dtos = Vec::with_capacity(pairs.len());
    let mut l_ids: Vec<&str> = Vec::with_capacity(pairs.len());
    let mut r_ids: Vec<&str> = Vec::with_capacity(pairs.len());
    let mut l_rows: Vec<i64> = Vec::with_capacity(pairs.len());
    let mut r_rows: Vec<i64> = Vec::with_capacity(pairs.len());
    for p in pairs {
        let lid = &left.row_ids[p.left_row as usize];
        let rid = &right.row_ids[p.right_row as usize];
        dtos.push(PairDto {
            left_id: lid.clone(),
            right_id: rid.clone(),
        });
        l_ids.push(lid.as_str());
        r_ids.push(rid.as_str());
        l_rows.push(i64::from(p.left_row));
        r_rows.push(i64::from(p.right_row));
    }
    let chunk = Chunk::new(vec![
        Box::new(Utf8Array::<i32>::from_slice(l_ids)) as Box<dyn Array>,
        Box::new(Utf8Array::<i32>::from_slice(r_ids)) as Box<dyn Array>,
        Box::new(Int64Array::from_slice(l_rows)) as Box<dyn Array>,
        Box::new(Int64Array::from_slice(r_rows)) as Box<dyn Array>,
    ]);
    (dtos, chunk)
}

/// Stable fingerprint of an immutable query definition.
#[must_use]
pub fn fingerprint(plan: &crate::operator::JoinPlan, left: &Batch, right: &Batch) -> String {
    let mut h = DefaultHasher::new();
    fn hash_batch(b: &Batch, h: &mut DefaultHasher) {
        b.row_ids.hash(h);
        for c in &b.columns {
            c.name.hash(h);
            c.values.hash(h);
        }
    }
    serde_json::to_string(plan).unwrap_or_default().hash(&mut h);
    hash_batch(left, &mut h);
    hash_batch(right, &mut h);
    format!("{:016x}", h.finish())
}

fn input_summary(
    plan: &crate::operator::JoinPlan,
    l: &Batch,
    r: &Batch,
    prep: Option<&PreparedJoin>,
) -> serde_json::Value {
    let mut v = serde_json::json!({
        "plan": plan,
        "left_rows": l.row_count(),
        "right_rows": r.row_count(),
        "left_columns": l.columns.iter().map(|c| c.name.clone()).collect::<Vec<_>>(),
        "right_columns": r.columns.iter().map(|c| c.name.clone()).collect::<Vec<_>>(),
    });
    if let (Some(p), serde_json::Value::Object(ref mut m)) = (prep, &mut v) {
        m.insert("prepared".to_owned(), p.describe());
    }
    v
}

/// Everything needed to record a run, owned so prepare/output can be
/// referenced without threading a dozen arguments.
struct RunCtx {
    run_id: String,
    budget: Budget,
    plan: crate::operator::JoinPlan,
    left: Batch,
    right: Batch,
}

/// Run-variable fields written into one [`RunRecord`].
struct RecordData<'a> {
    prep: Option<&'a PreparedJoin>,
    outcome: Outcome,
    events: Vec<crate::trace::TraceEvent>,
    counters: &'a Counters,
    pairs_emitted: u64,
    error: Option<&'a JoinError>,
}

impl RunCtx {
    fn finish(&self, tracer: &Tracer, d: RecordData<'_>) {
        let rec = RunRecord {
            run_id: self.run_id.clone(),
            started_unix_ms: now_ms(),
            outcome: d.outcome,
            error: d.error.map(ErrorSnapshot::from),
            budget: self.budget.clone(),
            input_summary: input_summary(&self.plan, &self.left, &self.right, d.prep),
            events: d.events,
            counters: d.counters.clone(),
            pairs_emitted: d.pairs_emitted,
        };
        if let Err(e) = tracer.persist(&rec) {
            tracing::warn!(run_id = self.run_id, error = %e, "failed to persist trace");
        }
    }
}

fn now_ms() -> u128 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis())
}

/// Result bundle of a one-shot execution.
pub struct OneShot {
    pub run_id: String,
    pub pairs: Vec<MatchPair>,
    pub counters: Counters,
    pub truncated: bool,
    pub events: Vec<crate::trace::TraceEvent>,
    pub chunk: Chunk<Box<dyn Array>>,
    pub pair_dtos: Vec<PairDto>,
}

impl std::fmt::Debug for OneShot {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OneShot")
            .field("run_id", &self.run_id)
            .field("pairs", &self.pairs)
            .field("counters", &self.counters)
            .field("truncated", &self.truncated)
            .field("events_len", &self.events.len())
            .field("arrow_rows", &self.chunk.len())
            .finish()
    }
}

/// Validate-then-run a one-shot query, tracing success/truncation and
/// every categorized failure (tagged with the run id).
///
/// # Errors
/// Propagates the categorized [`JoinError`] after recording it.
pub fn execute_oneshot(parsed: ParsedJoin, tracer: &Tracer) -> JoinResult<OneShot> {
    let ParsedJoin {
        plan,
        left,
        right,
        budget,
        include_trace,
    } = parsed;
    let ctx = RunCtx {
        run_id: tracer.new_run_id(),
        budget,
        plan,
        left,
        right,
    };

    let outcome = PreparedJoin::prepare(&ctx.plan, &ctx.left, &ctx.right).and_then(|prep| {
        let out = prep.run_all(ctx.budget.max_output_pairs)?;
        let (pair_dtos, chunk) = render_page(&ctx.left, &ctx.right, &out.pairs);
        Ok((prep, out, pair_dtos, chunk))
    });

    match outcome {
        Ok((prep, out, pair_dtos, chunk)) => {
            ctx.finish(
                tracer,
                RecordData {
                    prep: Some(&prep),
                    outcome: if out.truncated {
                        Outcome::Truncated
                    } else {
                        Outcome::Completed
                    },
                    events: out.events.clone(),
                    counters: &out.counters,
                    pairs_emitted: out.pairs.len() as u64,
                    error: None,
                },
            );
            Ok(OneShot {
                run_id: ctx.run_id,
                pairs: out.pairs,
                counters: out.counters,
                truncated: out.truncated,
                events: if include_trace {
                    out.events
                } else {
                    Vec::new()
                },
                chunk,
                pair_dtos,
            })
        }
        Err(e) => {
            let tagged = e.with_run(&ctx.run_id);
            ctx.finish(
                tracer,
                RecordData {
                    prep: None,
                    outcome: Outcome::Failed,
                    events: Vec::new(),
                    counters: &Counters::default(),
                    pairs_emitted: 0,
                    error: Some(&tagged),
                },
            );
            Err(tagged)
        }
    }
}

/// Result of opening a cursor: ids and the first page.
pub struct OpenCursor {
    pub cursor_id: String,
    pub run_id: String,
    pub page: CursorPageResponse,
}

impl std::fmt::Debug for OpenCursor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OpenCursor")
            .field("cursor_id", &self.cursor_id)
            .field("run_id", &self.run_id)
            .field("page_count", &self.page.count)
            .field("finished", &self.page.finished)
            .finish()
    }
}

fn initial_state() -> ExecutionState {
    ExecutionState {
        scan_pos: 0,
        gate_cursor: 0,
        bitmap_words: Vec::new(),
        bitmap_len: 0,
        resume_pos2: u32::MAX,
        emitted: 0,
    }
}

fn is_fresh(s: &ExecutionState) -> bool {
    s.scan_pos == 0 && s.resume_pos2 == u32::MAX && s.bitmap_len == 0
}

/// Create a cursor session and return its first page.
///
/// # Errors
/// `Input` for a zero page size; preparation errors propagate.
pub fn open_cursor(
    parsed: ParsedJoin,
    page_size: Option<u64>,
    store: &SessionStore,
    tracer: &Tracer,
) -> JoinResult<OpenCursor> {
    let run_id = tracer.new_run_id();
    let ParsedJoin {
        plan,
        left,
        right,
        budget,
        ..
    } = parsed;
    let page_size = page_size.unwrap_or(budget.max_output_pairs);
    validate_page_size(page_size, &budget, &run_id)?;

    let fp = fingerprint(&plan, &left, &right);
    let cursor_id = format!("cur-{run_id}");
    store.create(
        cursor_id.clone(),
        CursorSession {
            run_id: run_id.clone(),
            plan,
            left,
            right,
            budget,
            state: initial_state(),
            finished: false,
            batches_yielded: 0,
            fingerprint: fp,
        },
    );

    let page = advance(cursor_id.clone(), Some(page_size), store, tracer)?;
    Ok(OpenCursor {
        cursor_id,
        run_id,
        page,
    })
}

fn validate_page_size(page_size: u64, budget: &Budget, run_id: &str) -> JoinResult<()> {
    if page_size == 0 || page_size > budget.max_output_pairs {
        return Err(JoinError::input(
            "invalid_page_size",
            format!("page_size must be in 1..={}", budget.max_output_pairs),
        )
        .with("page_size", serde_json::json!(page_size))
        .with_run(run_id));
    }
    Ok(())
}

/// Advance a cursor by one controlled page.
///
/// # Errors
/// `StateConflict` for an unknown/closed cursor; `ResourceExhausted`
/// when the per-cursor page cap is reached.
pub fn advance(
    cursor_id: String,
    page_size: Option<u64>,
    store: &SessionStore,
    tracer: &Tracer,
) -> JoinResult<CursorPageResponse> {
    // Snapshot immutable inputs + checkpoint under the lock, then run
    // without holding it.
    let snap = store.with(&cursor_id, |s| {
        ensure_open(s)?;
        Ok((
            s.run_id.clone(),
            s.plan.clone(),
            s.left.clone(),
            s.right.clone(),
            s.budget.clone(),
            s.state.clone(),
            s.batches_yielded,
        ))
    })??;
    let (run_id, plan, left, right, budget, state, batches_seen) = snap;

    if batches_seen >= budget.max_batches_per_cursor {
        return Err(JoinError::exhausted(
            "cursor_batch_limit",
            format!(
                "cursor yielded {batches_seen} pages, limit is {}",
                budget.max_batches_per_cursor
            ),
        )
        .with_run(&run_id));
    }
    let limit = page_size.unwrap_or(budget.max_output_pairs);
    validate_page_size(limit, &budget, &run_id)?;

    let prep = PreparedJoin::prepare(&plan, &left, &right).map_err(|e| e.with_run(&run_id))?;
    let mut runner: JoinRunner<'_> = if is_fresh(&state) {
        prep.runner()
    } else {
        prep.resume(&state).map_err(|e| e.with_run(&run_id))?
    };
    let page = runner
        .next_page(limit as usize)
        .map_err(|e| e.with_run(&run_id))?;
    let (pair_dtos, _chunk) = render_page(&left, &right, &page.pairs);
    let counters: CountersDto = page.counters.into();
    let finished = page.finished;
    let count = page.pairs.len();
    let new_state = runner.checkpoint();

    // Commit checkpoint, re-checking the cursor is still open.
    store.with_mut(&cursor_id, |s| -> JoinResult<()> {
        ensure_open(s)?;
        s.state = new_state;
        s.batches_yielded += 1;
        s.finished = finished;
        Ok(())
    })??;

    if finished {
        store.remove(&cursor_id);
    }

    let _ = tracer; // page-level trace sink hook (per-run traces cover execution)
    Ok(CursorPageResponse {
        run_id,
        cursor_id,
        pairs: pair_dtos,
        count,
        finished,
        counters,
    })
}
