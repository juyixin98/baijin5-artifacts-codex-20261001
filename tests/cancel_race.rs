//! Cancellation races: cancel can land at any point relative to pulls.
//! Whichever way the race resolves, the outcome must be one of two clean
//! results — full completion or a single Cancelled error — with idempotent
//! close and full resource reclamation. No panics, no double frees.

mod common;

use std::time::Duration;

use pullq::error::{CancelKind, ErrorCategory};
use pullq::exec::RunId;
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::{Operator, OperatorState};

use common::{assert_resources_released, make_ctx, tlog};

#[tokio::test]
async fn cancel_racing_pulls_has_clean_outcome_and_idempotent_close() {
    let run_id = RunId::new().to_string();
    let mut completed = 0usize;
    let mut cancelled = 0usize;

    for round in 0..20 {
        let (ctx, resources, _dir) = make_ctx(1 << 20, None);
        let mut scan = ScanOperator::new(
            ScanConfig {
                table: "numbers".into(),
                batches: 40,
                batch_rows: 8,
                seed: round,
                delay_per_batch: Duration::from_millis(2),
                fail_at_batch: None,
            },
            std::sync::Arc::clone(&ctx),
        )
        .expect("scan");

        // Cancel at a deterministic but varying point in the stream.
        let token = ctx.cancel.clone();
        let cancel_after = Duration::from_millis(round * 4);
        let canceller = tokio::spawn(async move {
            tokio::time::sleep(cancel_after).await;
            token.cancel(CancelKind::User);
        });

        let mut batches = 0usize;
        let outcome: Result<(), ErrorCategory> = loop {
            match scan.next_batch().await {
                Ok(Some(_)) => batches += 1,
                Ok(None) => break Ok(()),
                Err(e) => break Err(e.category()),
            }
        };

        match outcome {
            Ok(()) => {
                completed += 1;
                tlog!(run_id, "round {round}: completed all {batches} batches before cancel landed");
            }
            Err(category) => {
                cancelled += 1;
                assert_eq!(
                    category,
                    ErrorCategory::CancelledUser,
                    "[{run_id}] round {round}: a racing cancel may only surface as CancelledUser, got {category:?} after {batches} batches"
                );
                tlog!(run_id, "round {round}: cancelled after {batches} batches (cancel_after={cancel_after:?})");
                // Stream is poisoned: further polls are state conflicts.
                let err = scan.next_batch().await.expect_err("poisoned after cancel");
                assert_eq!(err.category(), ErrorCategory::StateConflict, "[{run_id}] round {round}: poll after cancel error");
            }
        }

        // Close is idempotent even right after an error/cancel race.
        scan.close().await.expect("first close");
        scan.close().await.expect("second close must be a no-op");
        assert_eq!(scan.state(), OperatorState::Closed, "[{run_id}] round {round}: closed state");
        canceller.await.expect("canceller joins");
        ctx.shutdown().await;
        assert_resources_released(&run_id, &resources.snapshot(), &format!("round {round}"));
    }

    tlog!(run_id, "race summary: {completed} completed, {cancelled} cancelled out of 20 rounds");
    assert!(cancelled > 0, "[{run_id}] at least some rounds must exercise the cancel path");
}

#[tokio::test]
async fn double_cancel_keeps_first_reason() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, Some(Duration::from_millis(20)));
    ctx.cancel.cancel(CancelKind::User);
    // Deadline fires later and must not overwrite the user reason.
    tokio::time::sleep(Duration::from_millis(60)).await;
    let err = ctx.cancel.check().expect_err("cancelled");
    tlog!(run_id, "after user+timeout cancel: category={:?}", err.category());
    assert_eq!(err.category(), ErrorCategory::CancelledUser, "[{run_id}] first cancel reason wins");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "double cancel");
}
