//! Structured run diagnostics: a stable run id, key intermediate states, and
//! the reasoning behind terminal decisions. Every test asserts on these fields,
//! so a failing run can be replayed from the log alone.

use std::sync::Mutex;

use crate::error::ErrorKind;

/// Severity of a single diagnostic event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Info,
    Warn,
    Error,
}

/// One timestamped event in a run.
#[derive(Debug, Clone)]
pub struct Event {
    pub seq: u64,
    pub level: Level,
    pub operator: String,
    pub state: String,
    pub detail: String,
}

/// Accumulates everything needed to replay a single query execution.
///
/// A monotonically increasing [`RunId`] is assigned so parallel tests never
/// confuse logs; `op_states` records the last known lifecycle state of each
/// operator (useful for proving that a cancelled/errored operator still reached
/// `Closed`), and `terminal` records *why* the run stopped.
pub struct RunDiag {
    run_id: RunId,
    events: Mutex<Vec<Event>>,
    op_states: Mutex<Vec<(String, String)>>,
    terminal: Mutex<Option<Terminal>>,
}

/// Opaque, process-unique run identifier.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RunId(u64);

impl RunId {
    pub fn get(self) -> u64 {
        self.0
    }
}

impl std::fmt::Display for RunId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "run-{:06}", self.0)
    }
}

/// How a run ended.
#[derive(Debug, Clone)]
pub struct Terminal {
    pub kind: TerminalKind,
    pub reason: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TerminalKind {
    Completed,
    Failed(ErrorKind),
}

static RUN_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

impl RunDiag {
    pub fn new() -> std::sync::Arc<Self> {
        let run_id = RunId(RUN_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst));
        std::sync::Arc::new(Self {
            run_id,
            events: Mutex::new(Vec::new()),
            op_states: Mutex::new(Vec::new()),
            terminal: Mutex::new(None),
        })
    }

    pub fn run_id(&self) -> RunId {
        self.run_id
    }

    fn push(&self, level: Level, operator: &str, state: &str, detail: String) {
        let mut events = self.events.lock().expect("diag events lock poisoned");
        let seq = events.len() as u64 + 1;
        events.push(Event {
            seq,
            level,
            operator: operator.to_string(),
            state: state.to_string(),
            detail,
        });
    }

    pub fn info(&self, operator: &str, state: &str, detail: impl Into<String>) {
        self.push(Level::Info, operator, state, detail.into());
    }
    pub fn warn(&self, operator: &str, state: &str, detail: impl Into<String>) {
        self.push(Level::Warn, operator, state, detail.into());
    }
    pub fn error(&self, operator: &str, state: &str, detail: impl Into<String>) {
        self.push(Level::Error, operator, state, detail.into());
    }

    /// Record/replace the current lifecycle state of one operator.
    pub fn set_state(&self, operator: &str, state: &str) {
        let mut states = self.op_states.lock().expect("diag states lock poisoned");
        if let Some(slot) = states.iter_mut().find(|(name, _)| name == operator) {
            slot.1 = state.to_string();
        } else {
            states.push((operator.to_string(), state.to_string()));
        }
    }

    pub fn state_of(&self, operator: &str) -> Option<String> {
        self.op_states
            .lock()
            .expect("diag states lock poisoned")
            .iter()
            .find(|(name, _)| name == operator)
            .map(|(_, s)| s.clone())
    }

    /// Record the terminal outcome exactly once. A later, different claim is
    /// kept as a warning event but does not overwrite the first.
    pub fn terminate(&self, kind: TerminalKind, reason: impl Into<String>) {
        let reason = reason.into();
        let mut terminal = self.terminal.lock().expect("diag terminal lock poisoned");
        if terminal.is_none() {
            *terminal = Some(Terminal { kind, reason });
        } else {
            drop(terminal);
            self.warn("diag", "terminal_ignored", reason);
        }
    }

    pub fn terminal(&self) -> Option<Terminal> {
        self.terminal
            .lock()
            .expect("diag terminal lock poisoned")
            .clone()
    }

    pub fn events(&self) -> Vec<Event> {
        self.events
            .lock()
            .expect("diag events lock poisoned")
            .clone()
    }

    /// A compact, replay-oriented rendering.
    pub fn render(&self) -> String {
        let mut out = String::new();
        out.push_str(&format!("=== {} ===\n", self.run_id));
        for e in self.events() {
            out.push_str(&format!(
                "#{:03} {:5} [{}] {} — {}\n",
                e.seq,
                match e.level {
                    Level::Info => "INFO",
                    Level::Warn => "WARN",
                    Level::Error => "ERROR",
                },
                e.operator,
                e.state,
                e.detail,
            ));
        }
        if let Some(t) = self.terminal() {
            out.push_str(&format!("TERMINAL {:?}: {}\n", t.kind, t.reason));
        }
        out
    }
}
