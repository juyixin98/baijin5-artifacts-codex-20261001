//! IEJoin-style batch range join over two inequality predicates.
//!
//! Canonical form per predicate: `smaller_key <|<= greater_key`. The greater
//! side of predicate 1 is the *driver*; its rows are scanned ascending on the
//! predicate-1 key. Rows of the smaller side (the *active* side) enter a
//! bitmap monotonically as the driver key grows. The bitmap is indexed by the
//! active side's predicate-2 positions in descending order, so predicate 2 is
//! tested by scanning one contiguous prefix (active side is greater on p2) or
//! suffix (active side is smaller on p2), membership-probing the bitmap.
//!
//! Correctness pins:
//! * equality groups are never split: the activation boundary is a monotonic
//!   prefix and only depends on the driver key, so every driver row in an
//!   equal-key group sees the identical bitmap (strict/non-strict boundaries
//!   come from [`crate::permutation`]);
//! * duplicate-valued rows keep distinct row identities (stable permutations);
//! * NULL keys never match: NULL-p1 rows are never activated and NULL-p2
//!   positions lie outside every probe region;
//! * execution is resumable from a [`Checkpoint`], turning output truncation
//!   into controlled paging rather than a silently dropped tail.

use serde::{Deserialize, Serialize};

use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::operator::bitmap::BitMap;
use crate::operator::plan::{CanonicalPredicate, JoinPlan};
use crate::permutation::{SortOrder, SortedColumn};
use crate::resource::{Budget, JoinStats, Truncation};
use crate::types::value::KeyType;
use crate::types::{Scalar, TypedBatch};

/// One emitted pair, identified by physical row indices on each side.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct OutputPair {
    pub left_row: u64,
    pub right_row: u64,
}

/// Resumable execution position. Opaque to clients; serialized into cursors.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Checkpoint {
    /// Next driver position in driver ascending predicate-1 order.
    dpos: usize,
    /// Next active-side position to activate (ascending predicate-1 order).
    act_pos: usize,
    /// Next bitmap position to probe within the in-flight driver's region.
    probe_pos: usize,
    /// Probe region right edge for the in-flight driver row; `0` means the
    /// core is between driver rows and must compute a fresh region (a live
    /// region always has `probe_hi >= 1`).
    probe_hi: usize,
}

impl Checkpoint {
    pub fn start() -> Self {
        Self::default()
    }

    /// Expose the raw resumable coordinates (used by replay snapshots).
    pub fn parts(self) -> (usize, usize, usize, usize) {
        (self.dpos, self.act_pos, self.probe_pos, self.probe_hi)
    }

    /// Reconstruct a checkpoint from coordinates carried by a cursor.
    pub fn from_parts(dpos: usize, act_pos: usize, probe_pos: usize, probe_hi: usize) -> Self {
        Self {
            dpos,
            act_pos,
            probe_pos,
            probe_hi,
        }
    }
}

/// The compiled join: derived scalars, three sorted views and the canonical
/// predicates. Construction is the "compile" step; [`Self::run_page`] is the
/// resumable execution step.
#[derive(Debug)]
pub struct PreparedJoin {
    c1: CanonicalPredicate,
    c2: CanonicalPredicate,
    /// Whether the active side (smaller on p1) is also the smaller side of p2.
    active_smaller_on_p2: bool,
    /// Whether the active side is the left input batch.
    active_is_left: bool,

    n_driver: usize,
    n_active: usize,

    driver_asc_p1: SortedColumn,
    active_asc_p1: SortedColumn,
    active_desc_p2: SortedColumn,

    /// Predicate-1 key per driver row (physical order), activation thresholds.
    driver_p1: Vec<Scalar>,
    /// Predicate-2 key per driver row (physical order), probe thresholds.
    driver_p2: Vec<Scalar>,
}

impl PreparedJoin {
    /// Validate and prepare a plan against the two concrete batches.
    pub fn build(plan: &JoinPlan, left: &TypedBatch, right: &TypedBatch) -> JoinResult<Self> {
        plan.validate(left.column_count(), right.column_count())?;

        let (c1, c2) = plan.canonical();
        check_types(c1, left, right)?;
        check_types(c2, left, right)?;

        // Active side = smaller operand of predicate 1; driver = the other one.
        let active_is_left = c1.smaller_is_left;
        let (driver_batch, active_batch) = if active_is_left {
            (right, left)
        } else {
            (left, right)
        };
        let driver_is_left = !active_is_left;

        let driver_p1_col = side_column(c1, driver_is_left);
        let active_p1_col = side_column(c1, active_is_left);
        let driver_p2_col = side_column(c2, driver_is_left);
        let active_p2_col = side_column(c2, active_is_left);

        let driver_p1 = driver_batch
            .column(driver_p1_col)
            .expect("validated")
            .to_scalars();
        let driver_p2 = driver_batch
            .column(driver_p2_col)
            .expect("validated")
            .to_scalars();
        let active_p1 = active_batch
            .column(active_p1_col)
            .expect("validated")
            .to_scalars();
        let active_p2 = active_batch
            .column(active_p2_col)
            .expect("validated")
            .to_scalars();

        let driver_asc_p1 = SortedColumn::build(&driver_p1, SortOrder::Asc);
        let active_asc_p1 = SortedColumn::build(&active_p1, SortOrder::Asc);
        let active_desc_p2 = SortedColumn::build(&active_p2, SortOrder::Desc);

        Ok(Self {
            c1,
            c2,
            active_smaller_on_p2: c2.smaller_is_left == active_is_left,
            active_is_left,
            n_driver: driver_batch.row_count(),
            n_active: active_batch.row_count(),
            driver_asc_p1,
            active_asc_p1,
            active_desc_p2,
            driver_p1,
            driver_p2,
        })
    }

    pub fn driver_len(&self) -> usize {
        self.n_driver
    }
    pub fn active_len(&self) -> usize {
        self.n_active
    }
    pub fn active_is_left(&self) -> bool {
        self.active_is_left
    }

    /// Run (or resume) the join until the budget is consumed or the input is
    /// exhausted. Ordinary truncation is never an error: it is reported in
    /// [`JoinPage::truncation`] together with the checkpoint to resume.
    pub fn run_page(&self, budget: &Budget, from: Checkpoint) -> JoinPage {
        let mut bm = BitMap::new(self.n_active);
        let mut stats = JoinStats::default();
        let mut pairs: Vec<OutputPair> = Vec::new();

        let mut dpos = from.dpos;
        // The bitmap is rebuilt fresh on every (resumed) run, so activation
        // must reconstruct the whole prefix from the NULL boundary. The saved
        // `act_pos` is only cross-checked against the recomputed boundary.
        let mut act_pos = self.active_asc_p1.null_end();
        let mut probe_pos = from.probe_pos;
        let mut probe_hi = from.probe_hi;
        let mut truncation = Truncation::Complete;

        'outer: while dpos < self.n_driver {
            let driver_row = self.driver_asc_p1.permutation()[dpos];
            let key1 = &self.driver_p1[driver_row];

            // NULL driver keys sort first and never match anything.
            if key1.is_null() {
                dpos += 1;
                probe_pos = 0;
                probe_hi = 0;
                continue;
            }

            // 1) Monotonic activation of predicate-1 winners. Runs on every
            //    (re)entry — including resume — because the bitmap is rebuilt;
            //    a caught-up `act_pos` makes it a no-op. The boundary is the
            //    same for a whole equal-key driver group.
            let target = self.active_asc_p1.asc_prefix_len(key1, self.c1.strict);
            while act_pos < target {
                let active_row = self.active_asc_p1.permutation()[act_pos];
                bm.set(self.active_desc_p2.position_of(active_row));
                act_pos += 1;
            }

            let key2 = &self.driver_p2[driver_row];
            if key2.is_null() {
                dpos += 1;
                probe_pos = 0;
                probe_hi = 0;
                continue;
            }

            // 2) Predicate-2 probe region over the descending-p2 bitmap order.
            //    On resume (probe_hi > 0) the in-flight region is reused.
            if probe_hi == 0 {
                let region = if self.active_smaller_on_p2 {
                    // active.k2 <|<= driver.k2 → descending suffix.
                    self.active_desc_p2.match_interval(key2, self.c2.strict)
                } else {
                    // active.k2 >|>= driver.k2 → descending prefix.
                    let hi = self.active_desc_p2.desc_prefix_len(key2, self.c2.strict);
                    (hi > self.active_desc_p2.null_end())
                        .then_some((self.active_desc_p2.null_end(), hi))
                };
                match region {
                    Some((lo, hi)) => {
                        probe_pos = lo;
                        probe_hi = hi;
                    }
                    None => {
                        dpos += 1;
                        probe_pos = 0;
                        probe_hi = 0;
                        continue;
                    }
                }
            }

            // 3) Membership probes — each probe is one counted candidate access.
            while probe_pos < probe_hi {
                stats.candidate_accesses += 1;
                if bm.contains(probe_pos) {
                    let active_row = self.active_desc_p2.permutation()[probe_pos] as u64;
                    let driver_row_u = driver_row as u64;
                    let pair = if self.active_is_left {
                        OutputPair {
                            left_row: active_row,
                            right_row: driver_row_u,
                        }
                    } else {
                        OutputPair {
                            left_row: driver_row_u,
                            right_row: active_row,
                        }
                    };
                    pairs.push(pair);
                    stats.emitted += 1;
                    probe_pos += 1;
                    if stats.emitted >= budget.max_output {
                        truncation = Truncation::OutputLimit;
                        break 'outer;
                    }
                } else {
                    probe_pos += 1;
                }
                if stats.candidate_accesses >= budget.max_candidate_accesses {
                    truncation = Truncation::CandidateLimit;
                    break 'outer;
                }
            }

            dpos += 1;
            probe_pos = 0;
            probe_hi = 0;
        }

        stats.rows_driven = dpos;
        let finished = truncation == Truncation::Complete;
        let next = Checkpoint {
            dpos,
            act_pos,
            probe_pos,
            probe_hi,
        };
        JoinPage {
            pairs,
            stats,
            truncation,
            next,
            finished,
        }
    }
}

/// Column index on a physical side for a canonical predicate.
fn side_column(c: CanonicalPredicate, side_is_left: bool) -> usize {
    if side_is_left == c.smaller_is_left {
        c.smaller_col
    } else {
        c.greater_col
    }
}

fn check_types(c: CanonicalPredicate, left: &TypedBatch, right: &TypedBatch) -> JoinResult<()> {
    let lcol = left
        .column(side_column(c, true))
        .ok_or_else(|| JoinError::new(ErrorCode::MissingColumn, "left column missing"))?;
    let rcol = right
        .column(side_column(c, false))
        .ok_or_else(|| JoinError::new(ErrorCode::MissingColumn, "right column missing"))?;
    if lcol.key_type() != rcol.key_type() {
        return Err(JoinError::input(
            ErrorCode::IncompatibleTypes,
            format!(
                "predicate compares {} against {}",
                lcol.key_type().as_str(),
                rcol.key_type().as_str()
            ),
        ));
    }
    if !matches!(
        lcol.key_type(),
        KeyType::Int64 | KeyType::Float64 | KeyType::Utf8
    ) {
        return Err(JoinError::input(
            ErrorCode::UnsupportedType,
            format!(
                "type {} not supported for range join",
                lcol.key_type().as_str()
            ),
        ));
    }
    Ok(())
}

/// One page of join output plus continuation state.
#[derive(Debug, Clone)]
pub struct JoinPage {
    pub pairs: Vec<OutputPair>,
    pub stats: JoinStats,
    pub truncation: Truncation,
    pub next: Checkpoint,
    pub finished: bool,
}

/// Compute the complete join in one page; fail explicitly with a resource
/// error when the budget cannot hold it (callers needing controlled batching
/// drive [`PreparedJoin::run_page`] through the session layer instead).
pub fn join_full(
    plan: &JoinPlan,
    left: &TypedBatch,
    right: &TypedBatch,
    budget: &Budget,
) -> JoinResult<JoinPage> {
    let prepared = PreparedJoin::build(plan, left, right)?;
    let page = prepared.run_page(budget, Checkpoint::start());
    if !page.finished {
        return Err(JoinError::resource(
            ErrorCode::BudgetExceeded,
            format!(
                "join exceeds a single-page budget ({:?}); continue via a paged session",
                page.truncation
            ),
        ));
    }
    Ok(page)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::operator::nested_loop::nested_loop;
    use crate::operator::plan::{Comparator, Predicate};
    use crate::types::builder::{batch, float_column, int_column, utf8_column};

    fn plan(
        l1: usize,
        op1: Comparator,
        r1: usize,
        l2: usize,
        op2: Comparator,
        r2: usize,
    ) -> JoinPlan {
        JoinPlan::new(
            Predicate {
                left_col: l1,
                right_col: r1,
                op: op1,
            },
            Predicate {
                left_col: l2,
                right_col: r2,
                op: op2,
            },
        )
    }

    #[test]
    fn simple_strict_band_matches_nested_loop() {
        let l = batch(vec![
            int_column("a1", vec![Some(5)]),
            int_column("a2", vec![Some(10)]),
        ])
        .unwrap();
        let r = batch(vec![
            int_column("b1", vec![Some(1), Some(4), Some(6)]),
            int_column("b2", vec![Some(9), Some(11), Some(4)]),
        ])
        .unwrap();
        // a1 > b1 AND a2 < b2  => only (0,1): 5>4 and 10<11
        let p = plan(0, Comparator::Gt, 0, 1, Comparator::Lt, 1);
        let page = join_full(&p, &l, &r, &Budget::unlimited()).unwrap();
        assert_eq!(
            page.pairs,
            vec![OutputPair {
                left_row: 0,
                right_row: 1
            }]
        );
        assert_eq!(page.pairs, nested_loop(&p, &l, &r).unwrap());
    }

    #[test]
    fn utf8_and_float_keys_match_reference() {
        let l = batch(vec![
            utf8_column("a", vec![Some("m")]),
            utf8_column("x", vec![Some("q")]),
        ])
        .unwrap();
        let r = batch(vec![
            utf8_column("b", vec![Some("a"), Some("z")]),
            utf8_column("y", vec![Some("r"), Some("b")]),
        ])
        .unwrap();
        let p = plan(0, Comparator::Gt, 0, 1, Comparator::Lt, 1);
        let page = join_full(&p, &l, &r, &Budget::unlimited()).unwrap();
        assert_eq!(page.pairs, nested_loop(&p, &l, &r).unwrap());
        assert_eq!(
            page.pairs,
            vec![OutputPair {
                left_row: 0,
                right_row: 0
            }]
        );

        let lf = batch(vec![
            float_column("a", vec![Some(2.5)]),
            float_column("x", vec![Some(2.5)]),
        ])
        .unwrap();
        let rf = batch(vec![
            float_column("b", vec![Some(1.0), Some(2.5)]),
            float_column("y", vec![Some(3.0), Some(2.5)]),
        ])
        .unwrap();
        let pf = plan(0, Comparator::Gt, 0, 1, Comparator::Lt, 1);
        let pgp = join_full(&pf, &lf, &rf, &Budget::unlimited()).unwrap();
        assert_eq!(pgp.pairs, nested_loop(&pf, &lf, &rf).unwrap());
        assert_eq!(
            pgp.pairs,
            vec![OutputPair {
                left_row: 0,
                right_row: 0
            }]
        );
    }
}
