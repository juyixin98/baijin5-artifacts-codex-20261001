//! The recursive execution engine.
//!
//! Semi-naïve fixpoint loop over a frontier working table:
//!
//! 1. take the current new rows (the *only* rows rescanned this round),
//! 2. equi-join each against the edge relation (stable order),
//! 3. evaluate the recursive projection and extend the managed path,
//! 4. classify every child: branch-path cycle / global SET duplicate / fresh,
//! 5. enforce depth and row-count bounds with an explicit `Incomplete` verdict.
//!
//! Nothing is ever collapsed into success: validation errors return
//! `Failed` with a concrete [`FailureCategory`], bound overruns return
//! `Incomplete` with the precise reason.

use std::collections::BTreeMap;
use std::time::Instant;

use crate::batch::{Scalar, TypedBatch};
use crate::error::{EngineError, EngineResult};
use crate::exec::logger::RunLogger;
use crate::exec::seed::{assemble_seed_rows, extend_path, resolve_positions, ManagedPositions};
use crate::exec::sink::encode_ipc;
use crate::exec::state::{ChildVerdict, FrontierEntry, RunState};
use crate::operator::expr::{check_output_type, env_for, evaluate};
use crate::operator::join::{recursive_key_positions, EdgeIndex};
use crate::plan::{
    ColumnDecl, CycleConfig, CycleMode, IncompleteReason, RecursiveRequest, RecursiveResponse,
    RunStats, RunStatus, SetQuantifier, TraversalOrder,
};

/// Engine semantic version, echoed in every response and log line.
pub const ENGINE_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Immutable per-run evaluation context.
struct EvalCtx<'a> {
    req: &'a RecursiveRequest,
    schema: &'a [ColumnDecl],
    edge_schema: &'a [ColumnDecl],
    project: BTreeMap<String, &'a crate::plan::Expr>,
    positions: Option<ManagedPositions>,
    dedup_sets: bool,
}

impl<'a> EvalCtx<'a> {
    fn cycle(&self) -> Option<&'a CycleConfig> {
        self.req.cycle.as_ref()
    }

    /// Project one joined pair into a full-width child row with path extended
    /// and cycle marker reset to false.
    fn project_child(
        &self,
        parent: &FrontierEntry,
        edge_row: &[Scalar],
    ) -> EngineResult<Vec<Scalar>> {
        let rec_env = env_for(self.schema, &parent.row);
        let edge_env = env_for(self.edge_schema, edge_row);
        let mut child = vec![Scalar::Null; self.schema.len()];

        for decl in self.schema {
            let managed = match self.positions {
                Some(p) => {
                    decl.name == self.req.view.columns[p.path].name
                        || decl.name == self.req.view.columns[p.cycle].name
                }
                None => false,
            };
            if managed {
                continue;
            }
            let expr = self.project.get(&decl.name).copied().ok_or_else(|| {
                EngineError::internal(format!("missing projection for '{}'", decl.name))
            })?;
            let value = evaluate(expr, &rec_env, &edge_env)?;
            check_output_type(&value, decl.data_type, &decl.name)?;
            let idx = self
                .schema
                .iter()
                .position(|c| c.name == decl.name)
                .expect("decl belongs to schema");
            child[idx] = value;
        }

        if let Some(p) = self.positions {
            child[p.path] = extend_path(&parent.row[p.path], &child[p.key])?;
            child[p.cycle] = Scalar::Bool(false);
        }
        Ok(child)
    }
}

/// Stack item for pre-order DFS.
enum DfsWork {
    /// A row already sunk into the result: its outgoing edges should expand.
    Expand(FrontierEntry),
    /// A projected candidate not yet classified: classify and emit on pop.
    Candidate { row: Vec<Scalar>, depth: usize },
}

/// Owns all mutable fixpoint state, keeping the traversal functions small.
struct FixpointRunner<'a> {
    state: &'a mut RunState,
    ctx: EvalCtx<'a>,
    edge_index: EdgeIndex<'a>,
    rec_key_pos: Vec<usize>,
    logger: RunLogger,
    stats: RunStats,
    stop: Option<IncompleteReason>,
    remaining_frontier: usize,
}

impl<'a> FixpointRunner<'a> {
    fn run(
        mut self,
        seeds: Vec<FrontierEntry>,
    ) -> EngineResult<(RunStatus, Option<IncompleteReason>, RunStats, RunLogger)> {
        match self.ctx.req.traversal_order {
            TraversalOrder::Bfs => self.run_bfs(seeds),
            TraversalOrder::Dfs => self.run_dfs(seeds),
        }?;

        let status = if self.stop.is_some() {
            RunStatus::Incomplete
        } else {
            RunStatus::Complete
        };
        self.logger.emit(
            "fixpoint",
            None,
            Some(self.remaining_frontier),
            None,
            None,
            None,
            match self.stop {
                None => "empty frontier: fixpoint reached within all declared bounds".to_string(),
                Some(reason) => format!(
                    "stopped before fixpoint due to {reason:?}; {} frontier item(s) not expanded",
                    self.remaining_frontier
                ),
            },
        );
        Ok((status, self.stop, self.stats, self.logger))
    }

    // --- BFS --------------------------------------------------------------

    fn run_bfs(&mut self, mut frontier: Vec<FrontierEntry>) -> EngineResult<()> {
        let mut round = 0usize;
        while !frontier.is_empty() && self.stop.is_none() {
            round += 1;
            let frontier_size = frontier.len();
            let mut next_round: Vec<(Vec<Scalar>, usize)> = Vec::new();
            let mut totals = Counters::default();

            'round: for (frontier_i, entry) in frontier.iter().enumerate() {
                self.stats.join_probes += 1;
                let at_boundary = entry.depth >= self.ctx.req.limits.max_depth;
                let mut sink = Vec::new();
                match self.expand_entry(entry, !at_boundary, &mut sink)? {
                    Ok(counters) => {
                        totals.add(&counters);
                        next_round.extend(sink);
                    }
                    Err(reason) => {
                        self.stop = Some(reason);
                        if matches!(reason, IncompleteReason::MaxDepth) {
                            self.logger.emit(
                                "limit_hit",
                                Some(entry.depth),
                                Some(frontier_size),
                                Some(self.state.result_row_count()),
                                None,
                                None,
                                format!(
                                    "max_depth={} reached: a further row would have been emitted; {} of {} frontier item(s) plus next round left unexplored; result is explicitly incomplete",
                                    self.ctx.req.limits.max_depth,
                                    frontier_size - frontier_i,
                                    frontier_size
                                ),
                            );
                        }
                        break 'round;
                    }
                }
            }

            self.logger.emit(
                "bfs_round",
                Some(round),
                Some(frontier_size),
                Some(totals.emitted),
                Some(totals.cycles),
                Some(totals.duplicates),
                format!(
                    "round {round}: expanded {frontier_size} frontier row(s); {} emitted, {} cycle marker(s), {} duplicate(s) suppressed; decision basis = {}",
                    totals.emitted,
                    totals.cycles,
                    totals.duplicates,
                    if self.ctx.dedup_sets {
                        "path membership (declared key), then global SET fingerprint"
                    } else {
                        "path membership only (UNION ALL preserves multiplicity)"
                    }
                ),
            );

            frontier = next_round
                .into_iter()
                .map(|(row, depth)| FrontierEntry { row, depth })
                .collect();
        }
        self.remaining_frontier = frontier.len();
        self.stats.iterations = round;
        Ok(())
    }

    // --- DFS --------------------------------------------------------------

    fn run_dfs(&mut self, seeds: Vec<FrontierEntry>) -> EngineResult<()> {
        let mut expansions = 0usize;
        let mut stack: Vec<DfsWork> = seeds.into_iter().map(DfsWork::Expand).collect();

        'dfs: while let Some(work) = stack.pop() {
            match work {
                DfsWork::Candidate { row, depth } => {
                    match self.classify_candidate(&row, depth)? {
                        CandidateFlow::Stop => break 'dfs,
                        // Pre-order: dive into the admitted child's subtree
                        // before any sibling still on the stack.
                        CandidateFlow::Dive => {
                            stack.push(DfsWork::Expand(FrontierEntry { row, depth }))
                        }
                        CandidateFlow::Halted => {}
                    }
                }
                DfsWork::Expand(entry) => {
                    self.stats.join_probes += 1;
                    expansions += 1;

                    if entry.depth >= self.ctx.req.limits.max_depth {
                        // Probe-only boundary: any candidate that would add
                        // output proves truncation.
                        let mut would_grow = false;
                        for edge_idx in self.edge_index.probe(&entry.row, &self.rec_key_pos) {
                            let edge_row = &self.edge_index.edge_batch().rows()[edge_idx];
                            let child = self.ctx.project_child(&entry, edge_row)?;
                            match self.state.classify(&child, self.ctx.dedup_sets) {
                                ChildVerdict::Duplicate => {}
                                ChildVerdict::Fresh => would_grow = true,
                                ChildVerdict::Cycle => {
                                    if self.cycle_is_error() || self.state.would_admit_cycle(&child)
                                    {
                                        would_grow = true;
                                    }
                                }
                            }
                            if would_grow {
                                break;
                            }
                        }
                        if would_grow {
                            self.stop = Some(IncompleteReason::MaxDepth);
                            self.logger.emit(
                                "limit_hit",
                                Some(entry.depth),
                                Some(stack.len()),
                                Some(self.state.result_row_count()),
                                None,
                                None,
                                format!(
                                    "max_depth={} reached with an unexplored descendant; result is explicitly incomplete",
                                    self.ctx.req.limits.max_depth
                                ),
                            );
                            break 'dfs;
                        }
                        continue;
                    }

                    let matched = self.edge_index.probe(&entry.row, &self.rec_key_pos);
                    let mut candidates: Vec<DfsWork> = Vec::with_capacity(matched.len());
                    for edge_idx in matched {
                        let edge_row = &self.edge_index.edge_batch().rows()[edge_idx];
                        let row = self.ctx.project_child(&entry, edge_row)?;
                        candidates.push(DfsWork::Candidate {
                            row,
                            depth: entry.depth + 1,
                        });
                    }
                    // Reverse so the first matching edge is visited first.
                    stack.extend(candidates.into_iter().rev());

                    if expansions % 64 == 0 {
                        self.logger.emit(
                            "dfs_progress",
                            Some(entry.depth),
                            Some(stack.len()),
                            Some(self.state.result_row_count()),
                            Some(self.state.cycles_marked),
                            Some(self.state.duplicates_suppressed),
                            format!("{expansions} expansions so far; stack size {}", stack.len()),
                        );
                    }
                }
            }
        }

        self.remaining_frontier = stack.len();
        self.stats.iterations = expansions;
        Ok(())
    }

    /// Classify and sink one popped DFS candidate (pre-order semantics).
    fn classify_candidate(&mut self, row: &[Scalar], depth: usize) -> EngineResult<CandidateFlow> {
        match self.state.classify(row, self.ctx.dedup_sets) {
            ChildVerdict::Duplicate => {
                self.state.note_duplicate();
                Ok(CandidateFlow::Halted)
            }
            ChildVerdict::Cycle => {
                self.handle_cycle(row, depth)?;
                if self.stop.is_some() {
                    Ok(CandidateFlow::Stop)
                } else {
                    Ok(CandidateFlow::Halted)
                }
            }
            ChildVerdict::Fresh => {
                if self.state.result_row_count() >= self.ctx.req.limits.max_rows {
                    self.stop = Some(IncompleteReason::MaxRows);
                    self.logger.emit(
                        "limit_hit",
                        Some(depth),
                        None,
                        Some(self.state.result_row_count()),
                        None,
                        None,
                        format!(
                            "max_rows={} reached; result is explicitly incomplete",
                            self.ctx.req.limits.max_rows
                        ),
                    );
                    return Ok(CandidateFlow::Stop);
                }
                self.state.register_fresh(row.to_vec());
                Ok(CandidateFlow::Dive)
            }
        }
    }

    // --- Shared edge expansion --------------------------------------------

    /// Expand one frontier entry. When `emit` is false this is a depth-bound
    /// probe: children are classified but nothing is sunk; a `Fresh`/emittable
    /// `Cycle` child yields `MaxDepth`, while pure duplicates change nothing.
    fn expand_entry(
        &mut self,
        entry: &FrontierEntry,
        emit: bool,
        out_fresh: &mut Vec<(Vec<Scalar>, usize)>,
    ) -> EngineResult<Result<Counters, IncompleteReason>> {
        let mut counters = Counters::default();
        let matched = self.edge_index.probe(&entry.row, &self.rec_key_pos);

        for edge_idx in matched {
            let edge_row = &self.edge_index.edge_batch().rows()[edge_idx];
            let child = self.ctx.project_child(entry, edge_row)?;
            let verdict = self.state.classify(&child, self.ctx.dedup_sets);

            if !emit {
                return Ok(match verdict {
                    ChildVerdict::Duplicate => Ok(counters),
                    ChildVerdict::Fresh => Err(IncompleteReason::MaxDepth),
                    ChildVerdict::Cycle => {
                        if self.cycle_is_error() || self.state.would_admit_cycle(&child) {
                            Err(IncompleteReason::MaxDepth)
                        } else {
                            Ok(counters)
                        }
                    }
                });
            }

            match verdict {
                ChildVerdict::Duplicate => {
                    self.state.note_duplicate();
                    counters.duplicates += 1;
                }
                ChildVerdict::Fresh => {
                    if self.state.result_row_count() >= self.ctx.req.limits.max_rows {
                        self.logger.emit(
                            "limit_hit",
                            Some(entry.depth + 1),
                            None,
                            Some(self.state.result_row_count()),
                            None,
                            None,
                            format!(
                                "max_rows={} reached before emitting next row; result is explicitly incomplete",
                                self.ctx.req.limits.max_rows
                            ),
                        );
                        return Ok(Err(IncompleteReason::MaxRows));
                    }
                    self.state.register_fresh(child.clone());
                    out_fresh.push((child, entry.depth + 1));
                    counters.emitted += 1;
                }
                ChildVerdict::Cycle => {
                    if self.cycle_is_error() {
                        return Err(EngineError::invalid_data(format!(
                            "cycle detected at depth {} on declared key '{}'; cycle.mode = error",
                            entry.depth + 1,
                            self.ctx
                                .cycle()
                                .map(|c| c.key_column.as_str())
                                .unwrap_or("?")
                        )));
                    }
                    if self.state.result_row_count() >= self.ctx.req.limits.max_rows {
                        self.logger.emit(
                            "limit_hit",
                            Some(entry.depth + 1),
                            None,
                            Some(self.state.result_row_count()),
                            None,
                            None,
                            format!(
                                "max_rows={} reached before emitting cycle marker; result is explicitly incomplete",
                                self.ctx.req.limits.max_rows
                            ),
                        );
                        return Ok(Err(IncompleteReason::MaxRows));
                    }
                    let key_display = key_display(&self.ctx, &child);
                    if self.state.admit_cycle(child) {
                        counters.cycles += 1;
                        self.logger.emit(
                            "cycle_marked",
                            Some(entry.depth + 1),
                            None,
                            None,
                            Some(1),
                            None,
                            format!(
                                "declared key '{key_display}' already present on this branch's ancestor path; marker row emitted, branch halted"
                            ),
                        );
                    } else {
                        counters.duplicates += 1;
                        self.logger.emit(
                            "cycle_marker_dedup",
                            Some(entry.depth + 1),
                            None,
                            None,
                            None,
                            Some(1),
                            format!("declared key '{key_display}' cycle marker already present; duplicate suppressed under UNION"),
                        );
                    }
                }
            }
        }
        Ok(Ok(counters))
    }

    /// Sink a cycle candidate discovered during DFS.
    fn handle_cycle(&mut self, row: &[Scalar], depth: usize) -> EngineResult<()> {
        if self.cycle_is_error() {
            return Err(EngineError::invalid_data(format!(
                "cycle detected at depth {depth} on declared key '{}'; cycle.mode = error",
                self.ctx
                    .cycle()
                    .map(|c| c.key_column.as_str())
                    .unwrap_or("?")
            )));
        }
        if self.state.result_row_count() >= self.ctx.req.limits.max_rows {
            self.stop = Some(IncompleteReason::MaxRows);
            self.logger.emit(
                "limit_hit",
                Some(depth),
                None,
                Some(self.state.result_row_count()),
                None,
                None,
                format!(
                    "max_rows={} reached before emitting cycle marker; result is explicitly incomplete",
                    self.ctx.req.limits.max_rows
                ),
            );
            return Ok(());
        }
        let key_display = key_display(&self.ctx, row);
        if self.state.admit_cycle(row.to_vec()) {
            self.logger.emit(
                "cycle_marked",
                Some(depth),
                None,
                None,
                Some(1),
                None,
                format!("declared key '{key_display}' already present on this branch's ancestor path; marker emitted, branch halted"),
            );
        }
        Ok(())
    }

    fn cycle_is_error(&self) -> bool {
        matches!(self.ctx.cycle().map(|c| c.mode), Some(CycleMode::Error))
    }
}

/// What a DFS candidate classification implies for traversal.
enum CandidateFlow {
    /// Fresh row sunk; expand it before siblings.
    Dive,
    /// Duplicate or cycle marker: branch does not continue.
    Halted,
    /// A bound was hit: stop the whole walk.
    Stop,
}

#[derive(Default)]
struct Counters {
    emitted: usize,
    cycles: usize,
    duplicates: usize,
}

impl Counters {
    fn add(&mut self, other: &Counters) {
        self.emitted += other.emitted;
        self.cycles += other.cycles;
        self.duplicates += other.duplicates;
    }
}

fn key_display(ctx: &EvalCtx<'_>, child: &[Scalar]) -> String {
    match ctx.positions {
        Some(p) => match &child[p.key] {
            Scalar::Int(i) => i.to_string(),
            Scalar::Utf8(s) => s.clone(),
            other => format!("{other:?}"),
        },
        None => "<no-key>".to_string(),
    }
}

fn project_map(req: &RecursiveRequest) -> BTreeMap<String, &crate::plan::Expr> {
    req.recursive_term
        .project
        .iter()
        .map(|p| (p.alias.clone(), &p.value))
        .collect()
}

/// Execute a validated request. `run_id` ties every log line to the call.
pub fn execute(req: &RecursiveRequest, run_id: &str) -> EngineResult<RecursiveResponse> {
    let started = Instant::now();
    crate::plan::validate_request(req)?;
    let mut logger = RunLogger::new(run_id);

    logger.emit(
        "validated",
        None,
        None,
        None,
        None,
        None,
        format!(
            "request '{}' accepted: quantifier={:?}, order={:?}, max_depth={}, max_rows={}",
            req.name.as_deref().unwrap_or("<unnamed>"),
            req.set_quantifier,
            req.traversal_order,
            req.limits.max_depth,
            req.limits.max_rows
        ),
    );

    // --- Typed base relations ----------------------------------------------
    let edges_rel = req
        .relations
        .get(&req.recursive_term.edges_relation)
        .ok_or_else(|| {
            EngineError::invalid_plan(format!(
                "edge relation '{}' missing",
                req.recursive_term.edges_relation
            ))
        })?;
    let edges = TypedBatch::from_relation(edges_rel)?;
    let edge_index = EdgeIndex::build(&edges, &req.recursive_term.on)?;
    let rec_key_pos = recursive_key_positions(
        &TypedBatch::empty(req.view.columns.clone()),
        &req.recursive_term.on,
    )?;

    // --- Seed ---------------------------------------------------------------
    let positions = resolve_positions(req).map(|(k, p, c)| ManagedPositions {
        key: k,
        path: p,
        cycle: c,
    });
    let seed_rows = assemble_seed_rows(req, positions)?;
    let seed_input = seed_rows.len();
    let dedup_sets = req.set_quantifier == SetQuantifier::Union;

    let schema = req.view.columns.clone();
    let (mut state, initial_frontier, seed_dups) = RunState::new(
        schema.clone(),
        seed_rows,
        req.cycle.as_ref(),
        positions.map(|p| p.key),
        positions.map(|p| p.path),
        positions.map(|p| p.cycle),
        dedup_sets,
    );
    let frontier0 = initial_frontier
        .into_iter()
        .map(|row| FrontierEntry { row, depth: 0 })
        .collect::<Vec<_>>();
    let frontier0_len = frontier0.len();

    logger.emit(
        "seed_loaded",
        Some(0),
        Some(frontier0_len),
        Some(state.result_row_count()),
        Some(0),
        Some(seed_dups),
        format!(
            "{seed_input} seed row(s) supplied, {} unique materialized, {} duplicate(s) suppressed; managed columns {}",
            frontier0_len,
            seed_dups,
            if positions.is_some() {
                "initialized (path=[key], cycle=false)"
            } else {
                "absent"
            }
        ),
    );

    // --- Fixpoint loop ------------------------------------------------------
    let (status, incomplete_reason, mut stats, mut finished_logger) =
        if state.result_row_count() > req.limits.max_rows {
            logger.emit(
                "limit_hit",
                Some(0),
                Some(frontier0_len),
                Some(state.result_row_count()),
                None,
                None,
                format!(
                    "seed alone ({}) exceeds max_rows={}; result is explicitly incomplete",
                    state.result_row_count(),
                    req.limits.max_rows
                ),
            );
            (
                RunStatus::Incomplete,
                Some(IncompleteReason::MaxRows),
                RunStats::default(),
                logger,
            )
        } else {
            let ctx = EvalCtx {
                req,
                schema: &schema,
                edge_schema: &edges_rel.columns,
                project: project_map(req),
                positions,
                dedup_sets,
            };
            let runner = FixpointRunner {
                state: &mut state,
                ctx,
                edge_index,
                rec_key_pos,
                logger,
                stats: RunStats::default(),
                stop: None,
                remaining_frontier: 0,
            };
            runner.run(frontier0)?
        };

    stats.seed_rows = frontier0_len;
    stats.output_rows = state.result_row_count();
    stats.cycles_marked = state.cycles_marked;
    stats.duplicates_suppressed += state.duplicates_suppressed;
    stats.elapsed_us = started.elapsed().as_micros();

    finished_logger.emit(
        "finished",
        None,
        None,
        Some(state.result_row_count()),
        Some(state.cycles_marked),
        Some(state.duplicates_suppressed),
        format!(
            "verdict={status:?}{}; output rows={}, join probes={}, elapsed_us={}",
            incomplete_reason
                .map(|r| format!(" ({r:?})"))
                .unwrap_or_default(),
            state.result_row_count(),
            stats.join_probes,
            stats.elapsed_us
        ),
    );

    // --- Sink ---------------------------------------------------------------
    let output = state.result().to_relation();
    let arrow_ipc_base64 = if req.include_arrow_ipc {
        Some(encode_ipc(state.result())?)
    } else {
        None
    };

    Ok(RecursiveResponse {
        run_id: run_id.to_string(),
        engine_version: ENGINE_VERSION.to_string(),
        status,
        incomplete_reason,
        stats,
        output,
        arrow_ipc_base64,
        log: if req.include_log {
            finished_logger.into_entries()
        } else {
            Vec::new()
        },
    })
}
