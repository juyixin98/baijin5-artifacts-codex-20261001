//! Crate-wide error type.

use std::fmt;

/// Application error categories. These are *operator* errors (I/O, config,
/// Arrow conversion). Contract-level failures (rejected requests, undetermined
/// cases) are represented by `validate::Category`, never by this error.
#[derive(Debug)]
pub enum AppError {
    /// Failure inside the Arrow2 typed-batch layer (schema/array/IPC).
    Arrow(String),
    /// Invalid local configuration (config file / env).
    Config(String),
    /// HTTP/server-side failure.
    Api(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::Arrow(m) => write!(f, "arrow error: {m}"),
            AppError::Config(m) => write!(f, "config error: {m}"),
            AppError::Api(m) => write!(f, "api error: {m}"),
        }
    }
}

impl std::error::Error for AppError {}

impl From<arrow2::error::Error> for AppError {
    fn from(e: arrow2::error::Error) -> Self {
        AppError::Arrow(e.to_string())
    }
}

impl From<crate::state::StateError> for AppError {
    fn from(e: crate::state::StateError) -> Self {
        AppError::Api(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, AppError>;
