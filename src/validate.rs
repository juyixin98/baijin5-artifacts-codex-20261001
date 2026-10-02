//! Validation entry point.
//!
//! Provides fully-wired scenarios (scan, blocking spill-sort, hash join, early
//! stop, blocking/cancel) that both the integration tests and the HTTP server
//! drive. Keeping construction here gives the data/error contracts a single
//! choke point and stops the server from hand-assembling operator trees.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::batch::{Batch, Scalar};
use crate::cancel::{CancellationToken, Control};
use crate::diag::{RunDiag, TerminalKind};
use crate::error::{QueryError, QueryResult};
use crate::fixture;
use crate::operator::Operator;
use crate::operators::{BlockingScan, HashJoin, JoinType, Scan, Sort};
use crate::resource::ResourceTracker;

/// Which pre-wired scenario to validate/run.
#[derive(Debug, Clone)]
pub enum Scenario {
    /// Scan users only.
    ScanUsers,
    /// Orders sorted by amount, forced through a tiny spill budget.
    SpillSort {
        rows: usize,
        batch_size: usize,
        budget: u64,
    },
    /// Orders join users on user_id = id, then sort by amount.
    JoinSort {
        order_rows: usize,
        batch_size: usize,
        budget: u64,
    },
    /// A blocking scan whose per-batch delay exceeds the deadline.
    Timeout {
        per_batch_delay: Duration,
        deadline: Duration,
    },
    /// A blocking scan that is cancelled from another thread after `cancel_at`.
    CancelRace {
        per_batch_delay: Duration,
        batches: usize,
        cancel_at: Duration,
    },
}

/// Result of running a scenario: data plus the raw pieces tests assert on.
pub struct ScenarioOutput {
    pub run_id: String,
    pub batches: Vec<Batch>,
    pub error: Option<QueryError>,
    pub tracker: Arc<ResourceTracker>,
    pub diag: Arc<RunDiag>,
    pub root_states_after_close: Vec<(String, String)>,
}

impl Scenario {
    fn spill_root() -> PathBuf {
        crate::exec::default_spill_root()
    }

    /// Build the operator tree for this scenario. Returns the root and shared
    /// tracker/diag so callers can drive cancellation themselves.
    pub fn build_tree(
        &self,
        _diag: Arc<RunDiag>,
        tracker: Arc<ResourceTracker>,
    ) -> QueryResult<Box<dyn Operator>> {
        match self {
            Scenario::ScanUsers => {
                let schema = fixture::users_schema();
                let batch = fixture::users_batch()?;
                Ok(Box::new(Scan::new("scan", schema, vec![batch])))
            }
            Scenario::SpillSort {
                rows,
                batch_size,
                budget,
            } => {
                let schema = fixture::orders_schema();
                let rows = fixture::orders_rows(*rows);
                let batches = fixture::batches(schema.clone(), &rows, (*batch_size).max(1))?;
                let scan = Scan::new("scan", schema.clone(), batches);
                let sort = Sort::new(
                    "sort",
                    Box::new(scan),
                    &["amount".to_string()],
                    *budget,
                    tracker,
                    Self::spill_root(),
                )?;
                Ok(Box::new(sort))
            }
            Scenario::JoinSort {
                order_rows,
                batch_size,
                budget,
            } => {
                // Left (probe): orders. Right (build): users.
                let oschema = fixture::orders_schema();
                let orows = fixture::orders_rows(*order_rows);
                let obatches = fixture::batches(oschema.clone(), &orows, (*batch_size).max(1))?;
                let left = Scan::new("orders_scan", oschema, obatches);

                let uschema = fixture::users_schema();
                let ubatch = fixture::users_batch()?;
                let right = Scan::new("users_scan", uschema, vec![ubatch]);

                let join = HashJoin::new(
                    "join",
                    Box::new(left),
                    Box::new(right),
                    &["user_id".to_string()],
                    &["id".to_string()],
                    JoinType::Inner,
                )?;
                // Joined schema: order_id,user_id,amount,region,id,name,active.
                // Sort on amount (still index 2 after the join).
                let sort = Sort::new(
                    "sort",
                    Box::new(join),
                    &["amount".to_string()],
                    *budget,
                    tracker,
                    Self::spill_root(),
                )?;
                Ok(Box::new(sort))
            }
            Scenario::Timeout {
                per_batch_delay,
                deadline,
            } => {
                let _ = deadline;
                let schema = fixture::users_schema();
                let batches = fixture::batches(schema.clone(), &fixture::users_rows(), 2)?;
                Ok(Box::new(BlockingScan::new(
                    "blocking_scan",
                    schema,
                    batches,
                    *per_batch_delay,
                )))
            }
            Scenario::CancelRace {
                per_batch_delay,
                batches,
                cancel_at: _,
            } => {
                let schema = fixture::orders_schema();
                let rows = fixture::orders_rows((*batches).max(1) * 3);
                let bs = fixture::batches(schema.clone(), &rows, 3)?;
                Ok(Box::new(BlockingScan::new(
                    "blocking_scan",
                    schema,
                    bs,
                    *per_batch_delay,
                )))
            }
        }
    }

    /// Run to completion, applying this scenario's control semantics (deadline
    /// or cross-thread cancellation) and then closing exactly once.
    pub fn run(&self) -> ScenarioOutput {
        self.run_with_token(CancellationToken::new())
    }

    pub fn run_with_token(&self, token: CancellationToken) -> ScenarioOutput {
        let diag = RunDiag::new();
        let tracker = ResourceTracker::new();

        let deadline = match self {
            Scenario::Timeout { deadline, .. } => Some(Instant::now() + *deadline),
            _ => None,
        };
        let ctrl = Control::new(token.clone(), deadline, diag.clone());

        let mut root = self
            .build_tree(diag.clone(), tracker.clone())
            .expect("scenario tree builds");

        // Cancellation race: flip the token from another thread.
        if let Scenario::CancelRace { cancel_at, .. } = self {
            let token2 = token.clone();
            let at = *cancel_at;
            std::thread::spawn(move || {
                std::thread::sleep(at);
                token2.cancel();
            });
        }

        let mut batches = Vec::new();
        let mut error: Option<QueryError> = None;
        loop {
            match root.next(&ctrl) {
                Ok(Some(b)) => batches.push(b),
                Ok(None) => break,
                Err(e) => {
                    error = Some(e);
                    break;
                }
            }
        }

        // Snapshot operator states before close for diagnostics.
        root.shutdown(Some(&ctrl));

        match &error {
            None => diag.terminate(TerminalKind::Completed, "scenario completed"),
            Some(e) => diag.terminate(
                TerminalKind::Failed(e.kind()),
                format!("{}: {}", e.kind(), e.message()),
            ),
        }

        ScenarioOutput {
            run_id: diag.run_id().to_string(),
            batches,
            error,
            tracker,
            diag,
            root_states_after_close: Vec::new(),
        }
    }
}

/// Convenience for tests: flatten output batches into rows of scalars.
pub fn output_rows(out: &ScenarioOutput) -> QueryResult<Vec<Vec<Scalar>>> {
    let mut rows = Vec::new();
    for b in &out.batches {
        rows.append(&mut crate::operators::batch_rows(b)?);
    }
    Ok(rows)
}

/// Drive a [`BlockingScan`] under an externally-controlled token (no deadline).
/// Used by the streaming HTTP endpoint to demonstrate user cancellation as
/// distinct from timeout. Drains until data/error, then closes exactly once.
pub fn run_blocking_scan(
    per_batch_delay: Duration,
    num_batches: usize,
    token: CancellationToken,
) -> ScenarioOutput {
    let diag = RunDiag::new();
    let tracker = ResourceTracker::new();
    let ctrl = Control::new(token, None, diag.clone());

    let schema = fixture::orders_schema();
    let rows = fixture::orders_rows(num_batches.max(1) * 3);
    let batches = fixture::batches(schema.clone(), &rows, 3).expect("fixture batches");
    let mut root: Box<dyn Operator> = Box::new(BlockingScan::new(
        "blocking_scan",
        schema,
        batches,
        per_batch_delay,
    ));

    let mut collected = Vec::new();
    let mut error: Option<QueryError> = None;
    loop {
        match root.next(&ctrl) {
            Ok(Some(b)) => collected.push(b),
            Ok(None) => break,
            Err(e) => {
                error = Some(e);
                break;
            }
        }
    }
    root.shutdown(Some(&ctrl));
    match &error {
        None => diag.terminate(TerminalKind::Completed, "blocking scan completed"),
        Some(e) => diag.terminate(
            TerminalKind::Failed(e.kind()),
            format!("{}: {}", e.kind(), e.message()),
        ),
    }
    ScenarioOutput {
        run_id: diag.run_id().to_string(),
        batches: collected,
        error,
        tracker,
        diag,
        root_states_after_close: Vec::new(),
    }
}
