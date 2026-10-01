//! Error contract. Every fallible module returns [`SetOpsError`]; the four top
//! level kinds are deliberately distinguishable because callers (CLI/HTTP/tests)
//! map them to different exit codes / HTTP status / retry policies.

use serde::Serialize;

/// Row/column location context carried with an error where available.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ErrorContext {
    pub run_id: Option<String>,
    /// 1-based input line (header is line 1) for source parsing errors.
    pub line: Option<usize>,
    /// Side / file / partition the error was observed in.
    pub location: Option<String>,
    pub detail: Option<String>,
}

impl ErrorContext {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn at(mut self, location: impl Into<String>) -> Self {
        self.location = Some(location.into());
        self
    }
    pub fn line(mut self, line: usize) -> Self {
        self.line = Some(line);
        self
    }
    pub fn detail(mut self, detail: impl Into<String>) -> Self {
        self.detail = Some(detail.into());
        self
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InputCode {
    /// Bad type name in a typed header, or unsupported type.
    UnknownType,
    /// A value could not be parsed for its declared column type.
    ParseValue,
    /// Row column count differs from the schema.
    ColumnCountMismatch,
    /// Left/right schemas differ in arity or column data types.
    SchemaMismatch,
    /// Schema/query references something invalid (empty column, unknown op).
    InvalidRequest,
    /// Fixture reference escapes the configured fixture root.
    FixturePathDenied,
    /// Fixture/file not found.
    NotFound,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceCode {
    /// In-memory budget exceeded while spilling is disabled.
    MemoryBudget,
    /// Distinct-key map for a partition cannot fit even after recursive splits.
    PartitionDepth,
    /// Multiplicity exceeded the configured count cap (u64 overflow guard).
    CountOverflow,
    /// Spill directory / disk IO failure.
    SpillIo,
    /// Output cannot be materialised within the configured bounds.
    OutputLimit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StateCode {
    /// Client supplied a run_id that already exists.
    RunIdConflict,
    /// Result/run handle unknown, expired or already finalized.
    UnknownRun,
    /// A spill/result frame is corrupt or was modified during the run.
    SpillCorrupted,
}

/// Top level error category. `#[serde]` drives the HTTP error envelope:
/// serializes as `{"kind":"input","code":"parse_value"}`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", content = "code", rename_all = "snake_case")]
pub enum ErrorKind {
    Input(InputCode),
    StateConflict(StateCode),
    ResourceExhausted(ResourceCode),
    Compute,
}

#[derive(Debug, thiserror::Error)]
#[error("{kind:?}: {message}")]
pub struct SetOpsError {
    pub kind: ErrorKind,
    pub message: String,
    pub context: ErrorContext,
}

impl SetOpsError {
    pub fn input(code: InputCode, message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Input(code),
            message: message.into(),
            context: ErrorContext::default(),
        }
    }
    pub fn state(code: StateCode, message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::StateConflict(code),
            message: message.into(),
            context: ErrorContext::default(),
        }
    }
    pub fn resource(code: ResourceCode, message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::ResourceExhausted(code),
            message: message.into(),
            context: ErrorContext::default(),
        }
    }
    pub fn compute(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Compute,
            message: message.into(),
            context: ErrorContext::default(),
        }
    }

    /// Attach context (builder style): `err.ctx(|c| c.at("L/p3"))`.
    pub fn ctx(mut self, f: impl FnOnce(ErrorContext) -> ErrorContext) -> Self {
        self.context = f(self.context);
        self
    }

    /// Boundary helper: stamp a run id if none is present yet.
    pub fn with_run(mut self, run_id: &str) -> Self {
        if self.context.run_id.is_none() {
            self.context.run_id = Some(run_id.to_string());
        }
        self
    }

    pub fn is_input(&self) -> bool {
        matches!(self.kind, ErrorKind::Input(_))
    }
}

pub type Result<T> = std::result::Result<T, SetOpsError>;
