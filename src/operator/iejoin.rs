//! IEJoin core for two range predicates.
//!
//! # Algorithm
//!
//! Given `L.x <op1> R.x AND L.y <op2> R.y`:
//!
//! 1. Build **two** sort permutations of the **left** relation — one per
//!    predicate key — in the direction each comparator requires, plus a
//!    position map per permutation (NULL rows map to `None` and are
//!    never indexed).
//! 2. Build one scan permutation of the **right** relation on
//!    predicate 1's key, in the matching direction; iterate it. Rows
//!    NULL on either key are dropped (NULL never matches).
//! 3. A monotone, group-aware *gate* ([`super::permutation::GateCursor`])
//!    walks left permutation 1 as right values grow. It activates whole
//!    equality groups only when the boundary admits them: a strict
//!    boundary stops just below a group, a non-strict boundary enters
//!    it. This is the strict/non-strict distinction and the guarantee
//!    that no equal-value group is missed or admitted one row early.
//! 4. Activated left rows are translated through permutation 2's
//!    **position map** into one candidate bitmap (positions in
//!    permutation 2). The gate is monotone, so bits are only ever set:
//!    the bitmap always equals "left rows satisfying predicate 1 for
//!    the current right value".
//! 5. Predicate 2's satisfying set is a prefix `[0, bound2)` of
//!    permutation 2. Output pairs are exactly the set bits in that
//!    prefix. Bit update order therefore matters: a group's bits must
//!    all be present *before* the prefix is read — guaranteed by
//!    activating groups as units in step 3.
//!
//! Duplicate left rows occupy distinct original indices and hence
//! distinct bits; duplicate right rows are distinct scan rows, so full
//! Cartesian multiplicity is preserved while every row keeps its
//! caller-supplied identity.
//!
//! Two consumption shapes:
//!
//! * [`PreparedJoin::run_all`] — one-shot, with an explicit
//!   [`truncated`](JoinOutput::truncated) signal at the budget boundary;
//! * [`PreparedJoin::runner`] / [`JoinRunner::next_page`] — exact,
//!   mid-row resumable pagination via
//!   [`checkpoint`](JoinRunner::checkpoint).

use crate::batch::Batch;
use crate::error::{JoinError, JoinResult};
use crate::state::ExecutionState;
use crate::trace::TraceEvent;

use super::bitmap::{BitMap, Counters};
use super::permutation::{GateCursor, Permutation, SortSpec};
use super::plan::JoinPlan;

/// Upper bound on retained intermediate events per runner (the decision
/// sequence can be millions of rows long; the first events plus
/// counters suffice to replay a failure).
pub const EVENT_CAP: usize = 48;

/// One matched pair as original row positions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct MatchPair {
    pub left_row: u32,
    pub right_row: u32,
}

/// Result of a one-shot execution.
#[derive(Clone, Debug, Default)]
pub struct JoinOutput {
    pub pairs: Vec<MatchPair>,
    pub counters: Counters,
    /// `true` when the output budget stopped enumeration early.
    pub truncated: bool,
    pub events: Vec<TraceEvent>,
}

#[must_use]
fn spec_of(op: super::comparator::Comparator) -> SortSpec {
    SortSpec {
        ascending: op.right_greater(),
        strict: op.is_strict(),
    }
}

/// Built permutations and scan order; immutable once prepared.
pub struct PreparedJoin {
    perm1: Permutation,
    perm2: Permutation,
    spec1: SortSpec,
    spec2: SortSpec,
    /// Original right row indices in scan order (non-NULL on both keys).
    right_scan: Vec<u32>,
    /// Predicate-1 key value parallel to [`Self::right_scan`].
    right_keys1: Vec<i64>,
    /// Predicate-2 prefix boundary parallel to [`Self::right_scan`].
    bounds2: Vec<u32>,
    /// Binary-search probes used to find each predicate-2 boundary.
    bounds2_probes: Vec<u64>,
}

impl PreparedJoin {
    /// Validate the plan against the inputs and build both sorted
    /// permutations, position maps and the right scan.
    ///
    /// # Errors
    /// `Input` for an invalid plan or missing key columns.
    pub fn prepare(plan: &JoinPlan, left: &Batch, right: &Batch) -> JoinResult<PreparedJoin> {
        plan.validate()?;
        let [p1, p2] = plan.ordered();

        let lx = super::permutation::require_col(&left.columns, &p1.left_column)?;
        let ly = super::permutation::require_col(&left.columns, &p2.left_column)?;
        let rx = super::permutation::require_col(&right.columns, &p1.right_column)?;
        let ry = super::permutation::require_col(&right.columns, &p2.right_column)?;

        let spec1 = spec_of(p1.op);
        let spec2 = spec_of(p2.op);

        let perm1 = Permutation::build(lx, spec1.ascending)?;
        let perm2 = Permutation::build(ly, spec2.ascending)?;

        let mut scan_rows: Vec<u32> = (0..right.row_count() as u32)
            .filter(|&i| rx[i as usize].is_some() && ry[i as usize].is_some())
            .collect();
        scan_rows.sort_by(|&a, &b| {
            let va = rx[a as usize].expect("filtered non-null");
            let vb = rx[b as usize].expect("filtered non-null");
            let ord = va.cmp(&vb);
            if spec1.ascending { ord } else { ord.reverse() }.then_with(|| a.cmp(&b))
        });

        let right_keys1 = scan_rows
            .iter()
            .map(|&r| rx[r as usize].expect("non-null"))
            .collect();
        let bound_pairs: Vec<(u32, u64)> = scan_rows
            .iter()
            .map(|&r| {
                let (b, probes) =
                    perm2.satisfied_prefix_probed(ry[r as usize].expect("non-null"), spec2);
                (b as u32, probes)
            })
            .collect();
        let bounds2 = bound_pairs.iter().map(|(b, _)| *b).collect();
        let bounds2_probes: Vec<u64> = bound_pairs.iter().map(|(_, p)| *p).collect();

        Ok(PreparedJoin {
            perm1,
            perm2,
            spec1,
            spec2,
            right_scan: scan_rows,
            right_keys1,
            bounds2,
            bounds2_probes,
        })
    }

    /// Diagnostic summaries embedded in traces.
    #[must_use]
    pub fn describe(&self) -> serde_json::Value {
        serde_json::json!({
            "perm1_len": self.perm1.len(),
            "perm2_len": self.perm2.len(),
            "right_scan_len": self.right_scan.len(),
            "spec1": {"ascending": self.spec1.ascending, "strict": self.spec1.strict},
            "spec2": {"ascending": self.spec2.ascending, "strict": self.spec2.strict},
        })
    }

    /// One-shot materialization of at most `max_pairs` pairs. If more
    /// matches exist, enumeration stops cleanly at the boundary and
    /// [`JoinOutput::truncated`] is set — callers wanting everything
    /// should page with [`Self::runner`] instead.
    ///
    /// # Errors
    /// Input/structural errors only.
    pub fn run_all(&self, max_pairs: u64) -> JoinResult<JoinOutput> {
        let limit = usize::try_from(max_pairs).unwrap_or(usize::MAX);
        let mut runner = JoinRunner::fresh(self);
        let page = runner.next_page(limit)?;
        Ok(JoinOutput {
            pairs: page.pairs,
            counters: page.counters,
            truncated: !page.finished,
            events: page.events,
        })
    }

    /// Start a pageable enumerator.
    #[must_use]
    pub fn runner(&self) -> JoinRunner<'_> {
        JoinRunner::fresh(self)
    }

    /// Restore a pageable enumerator from a checkpoint.
    ///
    /// # Errors
    /// `Compute` if the checkpoint does not fit this preparation
    /// (bitmap shape or out-of-range positions).
    pub fn resume(&self, state: &ExecutionState) -> JoinResult<JoinRunner<'_>> {
        JoinRunner::from_checkpoint(self, state)
    }
}

/// One deterministic page of output.
#[derive(Clone, Debug)]
pub struct Page {
    pub pairs: Vec<MatchPair>,
    /// Work performed while producing *this* page (delta counters).
    pub counters: Counters,
    /// All scan rows consumed and no pending row remains.
    pub finished: bool,
    /// Key intermediate events observed on this page.
    pub events: Vec<TraceEvent>,
}

/// Exact, mid-row resumable enumerator over a [`PreparedJoin`].
pub struct JoinRunner<'a> {
    prep: &'a PreparedJoin,
    gate: GateCursor,
    candidates: BitMap,
    /// Next scan index to open.
    scan_pos: usize,
    /// Row currently being drained: `(scan_index, next perm-2 pos)`.
    pending: Option<(usize, usize)>,
    /// Cumulative emitted pairs (checkpoints/debug only).
    emitted: u64,
    finished: bool,
    step: u64,
    events: Vec<TraceEvent>,
}

impl<'a> JoinRunner<'a> {
    fn fresh(prep: &'a PreparedJoin) -> Self {
        Self {
            prep,
            gate: GateCursor::new(prep.spec1),
            candidates: BitMap::new(prep.perm2.len()),
            scan_pos: 0,
            pending: None,
            emitted: 0,
            finished: false,
            step: 0,
            events: Vec::new(),
        }
    }

    fn from_checkpoint(prep: &'a PreparedJoin, st: &ExecutionState) -> JoinResult<Self> {
        let expect_words = st.bitmap_len as usize / 64 + usize::from(st.bitmap_len % 64 != 0);
        if st.bitmap_words.len() != expect_words {
            return Err(JoinError::compute(
                "checkpoint_shape_mismatch",
                format!(
                    "checkpoint bitmap has {} words, expected {expect_words} for len {}",
                    st.bitmap_words.len(),
                    st.bitmap_len
                ),
            ));
        }
        if st.bitmap_len as usize != prep.perm2.len() {
            return Err(JoinError::compute(
                "checkpoint_shape_mismatch",
                format!(
                    "checkpoint bitmap len {} != prepared permutation len {}",
                    st.bitmap_len,
                    prep.perm2.len()
                ),
            ));
        }
        if st.scan_pos as usize > prep.right_scan.len() {
            return Err(JoinError::compute(
                "checkpoint_out_of_range",
                "checkpoint scan_pos lies beyond the right scan",
            ));
        }
        let candidates = BitMap::from_words(st.bitmap_words.clone(), st.bitmap_len as usize);
        let pending = if st.resume_pos2 == u32::MAX {
            None
        } else {
            // scan_pos already advanced past the in-flight row.
            if st.scan_pos == 0 {
                return Err(JoinError::compute(
                    "checkpoint_invalid",
                    "resume_pos2 set but scan_pos is 0",
                ));
            }
            Some((st.scan_pos as usize - 1, st.resume_pos2 as usize))
        };
        Ok(Self {
            prep,
            gate: GateCursor::at(prep.spec1, st.gate_cursor as usize),
            candidates,
            scan_pos: st.scan_pos as usize,
            pending,
            emitted: st.emitted,
            finished: false,
            step: 0,
            events: Vec::new(),
        })
    }

    /// Opaque checkpoint for resuming in another request/process.
    #[must_use]
    pub fn checkpoint(&self) -> ExecutionState {
        ExecutionState {
            scan_pos: self.scan_pos as u32,
            gate_cursor: self.gate.position() as u32,
            bitmap_words: self.candidates.words().to_vec(),
            bitmap_len: self.candidates.len() as u32,
            resume_pos2: match self.pending {
                Some((_, pos)) => pos as u32,
                None => u32::MAX,
            },
            emitted: self.emitted,
        }
    }

    #[must_use]
    pub fn emitted(&self) -> u64 {
        self.emitted
    }

    #[must_use]
    pub fn is_finished(&self) -> bool {
        self.finished
    }

    fn record(&mut self, kind: &str, rationale: String, state: serde_json::Value) {
        if self.events.len() < EVENT_CAP {
            self.step += 1;
            self.events.push(TraceEvent {
                step: self.step,
                kind: kind.to_owned(),
                rationale,
                state,
            });
        } else {
            self.step += 1;
        }
    }

    /// Produce the next page of at most `limit` pairs. Pausing is exact
    /// even in the middle of one right row, so resuming neither
    /// duplicates nor skips a pair. Counters on the page are deltas.
    ///
    /// # Errors
    /// Reserved for future typed-coercion failures; currently infallible.
    pub fn next_page(&mut self, limit: usize) -> JoinResult<Page> {
        let mut counters = Counters::default();
        let mut pairs: Vec<MatchPair> = Vec::with_capacity(limit.min(1024));
        let events_start = self.events.len();
        let gate_visits_before = self.gate.visits();

        'paging: loop {
            // Open the next scan row if none is in flight. Order
            // matters: check exhaustion BEFORE the page-full check so a
            // result count that exactly equals the limit and exhausts
            // the scan is reported as finished, not truncated.
            if self.pending.is_none() {
                if self.scan_pos >= self.prep.right_scan.len() {
                    self.finished = true;
                    break;
                }
                if pairs.len() >= limit {
                    break;
                }
                let i = self.scan_pos;
                self.scan_pos += 1;
                counters.right_rows_scanned += 1;

                let k1 = self.prep.right_keys1[i];
                let newly = self.gate.advance(&self.prep.perm1, k1);
                // The predicate-2 boundary is found once per scan row by
                // binary search at prepare time; charge its probes when
                // the row is first opened (rows are never reopened).
                counters.gate2_steps += self.prep.bounds2_probes[i];
                let mut mapped = 0u64;
                for &lrow in &newly {
                    if let Some(pos2) = self.prep.perm2.position[lrow as usize] {
                        self.candidates.set(pos2 as usize);
                        mapped += 1;
                    }
                }
                self.record(
                    "right_row",
                    format!(
                        "scan[{i}] right_row={} key1={k1}: gate activated {} group row(s), {mapped} mapped to bitmap; pred2 prefix={}",
                        self.prep.right_scan[i],
                        newly.len(),
                        self.prep.bounds2[i]
                    ),
                    serde_json::json!({
                        "scan_index": i,
                        "right_row": self.prep.right_scan[i],
                        "key1": k1,
                        "gate_cursor": self.gate.position(),
                        "newly_activated_rows": newly.len(),
                        "bitmap_ones": self.candidates.count_ones(),
                        "bound2": self.prep.bounds2[i],
                    }),
                );
                self.pending = Some((i, 0));
            }

            let (i, mut pos) = self.pending.expect("pending set above");
            let bound2 = self.prep.bounds2[i] as usize;

            let row_done = 'drain: loop {
                // Locate the next candidate at/after `pos` (a pure index
                // step), then *inspect* it. The inspection is what the
                // candidate_accesses counter charges for, including a
                // candidate that fails predicate 2 at the boundary.
                let Some(pos2) = self.candidates.next_one(pos) else {
                    break 'drain true;
                };
                if pos2 >= bound2 {
                    counters.candidate_accesses += 1;
                    break 'drain true;
                }
                if pairs.len() >= limit {
                    // Pause mid-row at this exact, not-yet-inspected
                    // position. It is deliberately not counted here: on
                    // resume it is located and inspected once, keeping
                    // access accounting independent of page size.
                    self.pending = Some((i, pos2));
                    break 'paging;
                }
                counters.candidate_accesses += 1;
                pairs.push(MatchPair {
                    left_row: self.prep.perm2.order[pos2],
                    right_row: self.prep.right_scan[i],
                });
                self.emitted += 1;
                counters.pairs_emitted += 1;
                pos = pos2 + 1;
            };
            if row_done {
                // Row fully drained (no more set bits, or the next bit
                // lies beyond the predicate-2 prefix).
                self.pending = None;
            }
        }

        counters.gate1_steps = self.gate.visits() - gate_visits_before;
        let events = self.events[events_start..].to_vec();
        Ok(Page {
            pairs,
            counters,
            finished: self.finished,
            events,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::Column;
    use crate::operator::comparator::Comparator;
    use crate::operator::plan::Predicate;

    fn plan(op1: Comparator, op2: Comparator) -> JoinPlan {
        JoinPlan::new(Predicate::new("x", op1, "x"), Predicate::new("y", op2, "y"))
    }

    fn batch(x: Vec<Option<i64>>, y: Vec<Option<i64>>) -> Batch {
        Batch::new(vec![Column::new("x", x), Column::new("y", y)]).unwrap()
    }

    fn pair_vec(out: &JoinOutput) -> Vec<(u32, u32)> {
        let mut v: Vec<_> = out
            .pairs
            .iter()
            .map(|p| (p.left_row, p.right_row))
            .collect();
        v.sort_unstable();
        v
    }

    #[test]
    fn empty_side() {
        let prep = PreparedJoin::prepare(
            &plan(Comparator::Lt, Comparator::Lt),
            &batch(vec![Some(1)], vec![Some(1)]),
            &batch(Vec::new(), Vec::new()),
        )
        .unwrap();
        let out = prep.run_all(100).unwrap();
        assert!(out.pairs.is_empty() && !out.truncated);
    }

    #[test]
    fn strict_and_lenient_boundary_intersection() {
        // L (x,y): (1,5),(2,4),(3,3) ; R (x,y): (3,4)
        // x<3 -> L rows {0,1}; y<4 -> {2}; intersection -> {}
        let prep = PreparedJoin::prepare(
            &plan(Comparator::Lt, Comparator::Lt),
            &batch(
                vec![Some(1), Some(2), Some(3)],
                vec![Some(5), Some(4), Some(3)],
            ),
            &batch(vec![Some(3)], vec![Some(4)]),
        )
        .unwrap();
        assert!(prep.run_all(100).unwrap().pairs.is_empty());

        // <=3 and <=4 -> x<={0,1,2}, y<=4 -> {1,2} => {1,2}
        let prep = PreparedJoin::prepare(
            &plan(Comparator::Le, Comparator::Le),
            &batch(
                vec![Some(1), Some(2), Some(3)],
                vec![Some(5), Some(4), Some(3)],
            ),
            &batch(vec![Some(3)], vec![Some(4)]),
        )
        .unwrap();
        assert_eq!(pair_vec(&prep.run_all(100).unwrap()), vec![(1, 0), (2, 0)]);
    }

    #[test]
    fn pagination_is_exact_across_midrow_pause() {
        // 4 identical left rows, 2 identical right rows all match => 8.
        let l = batch(vec![Some(1); 4], vec![Some(1); 4]);
        let r = batch(vec![Some(2); 2], vec![Some(2); 2]);
        let prep = PreparedJoin::prepare(&plan(Comparator::Lt, Comparator::Lt), &l, &r).unwrap();

        let mut runner = prep.runner();
        let mut all = Vec::new();
        let mut finished = false;
        for _ in 0..20 {
            let page = runner.next_page(3).unwrap();
            all.extend(page.pairs.iter().map(|p| (p.left_row, p.right_row)));
            if page.finished {
                finished = true;
                break;
            }
        }
        assert!(finished, "must terminate");
        all.sort_unstable();
        let expect: Vec<(u32, u32)> = (0..4u32)
            .flat_map(|l| (0..2u32).map(move |r| (l, r)))
            .collect();
        assert_eq!(all, expect);
    }

    #[test]
    fn checkpoint_roundtrip_resumes_without_loss_or_dup() {
        let l = batch(vec![Some(1); 3], vec![Some(1); 3]);
        let r = batch(vec![Some(2); 3], vec![Some(2); 3]); // 9 pairs
        let prep = PreparedJoin::prepare(&plan(Comparator::Le, Comparator::Le), &l, &r).unwrap();

        let mut runner = prep.runner();
        let p1 = runner.next_page(4).unwrap();
        assert!(!p1.finished);
        let cp = runner.checkpoint();

        let mut resumed = prep.resume(&cp).unwrap();
        let mut got: Vec<_> = p1.pairs.iter().map(|p| (p.left_row, p.right_row)).collect();
        loop {
            let pg = resumed.next_page(100).unwrap();
            got.extend(pg.pairs.iter().map(|p| (p.left_row, p.right_row)));
            if pg.finished {
                break;
            }
        }
        got.sort_unstable();
        let mut expect: Vec<_> = (0..3u32)
            .flat_map(|l| (0..3u32).map(move |r| (l, r)))
            .collect();
        expect.sort_unstable();
        assert_eq!(got, expect);
    }

    #[test]
    fn run_all_truncates_at_cap() {
        let l = batch(vec![Some(1); 8], vec![Some(1); 8]);
        let r = batch(vec![Some(2)], vec![Some(2)]);
        let prep = PreparedJoin::prepare(&plan(Comparator::Lt, Comparator::Lt), &l, &r).unwrap();
        let out = prep.run_all(5).unwrap();
        assert!(out.truncated);
        assert_eq!(out.pairs.len(), 5);
    }
}
