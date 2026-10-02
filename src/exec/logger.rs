//! Structured per-run logging.
//!
//! Every entry is tied to the run id and carries progress counters plus the
//! textual decision basis. The variadic argument count is intrinsic to a
//! structured log record (five optional counters + free text), so the
//! too-many-arguments lint is intentionally allowed on `emit`.

use crate::plan::RunLogEntry;

pub(crate) struct RunLogger {
    run_id: String,
    entries: Vec<RunLogEntry>,
}

impl RunLogger {
    pub(crate) fn new(run_id: &str) -> Self {
        Self {
            run_id: run_id.to_string(),
            entries: Vec::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn emit(
        &mut self,
        step: &str,
        depth: Option<usize>,
        frontier_rows: Option<usize>,
        emitted: Option<usize>,
        cycles_marked: Option<usize>,
        duplicates_suppressed: Option<usize>,
        detail: String,
    ) {
        self.entries.push(RunLogEntry {
            run_id: self.run_id.clone(),
            step: step.to_string(),
            depth,
            frontier_rows,
            emitted,
            cycles_marked,
            duplicates_suppressed,
            detail,
        });
    }

    pub(crate) fn into_entries(self) -> Vec<RunLogEntry> {
        self.entries
    }
}
