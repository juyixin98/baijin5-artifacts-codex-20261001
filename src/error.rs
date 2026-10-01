//! Typed error categories for validation and execution.
//!
//! Every failure the API can return is one of [`ErrorCategory`], so diagnostics
//! can state *why* a request was accepted, rejected, or could not be decided,
//! instead of surfacing a free-text string.
use std::fmt;

/// Broad, machine-readable failure class.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCategory {
    /// Hard reject: the request is malformed or violates a safety restriction.
    Validation,
    /// The system cannot decide right now (resources exhausted / limit
    /// reached), but the request itself is valid; resumption is possible.
    ResourceLimit,
    /// Deserialization / encoding boundary failure.
    Serde,
    /// Internal invariant violated — always a bug.
    Internal,
}

impl fmt::Display for ErrorCategory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            ErrorCategory::Validation => "validation",
            ErrorCategory::ResourceLimit => "resource_limit",
            ErrorCategory::Serde => "serde",
            ErrorCategory::Internal => "internal",
        };
        f.write_str(s)
    }
}

/// Stable machine codes attached to each error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    EmptyRelationList,
    TooManyRelations,
    EmptyRelation,
    EmptySchema,
    UnknownColumn,
    DuplicateColumn,
    ColumnArityMismatch,
    RowArityMismatch,
    TypeMismatch,
    UnsupportedJoinType,
    NullInJoinKey,
    DisconnectedJoinGraph,
    NoCommonAttribute,
    VariableNotBound,
    DuplicateVariable,
    BadLimit,
    InvalidCursor,
    MissingRelation,
    RelationNameMismatch,
    EncodeFailed,
    InternalInvariant,
}

impl ErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::EmptyRelationList => "empty_relation_list",
            ErrorCode::TooManyRelations => "too_many_relations",
            ErrorCode::EmptyRelation => "empty_relation",
            ErrorCode::EmptySchema => "empty_schema",
            ErrorCode::UnknownColumn => "unknown_column",
            ErrorCode::DuplicateColumn => "duplicate_column",
            ErrorCode::ColumnArityMismatch => "column_arity_mismatch",
            ErrorCode::RowArityMismatch => "row_arity_mismatch",
            ErrorCode::TypeMismatch => "type_mismatch",
            ErrorCode::UnsupportedJoinType => "unsupported_join_type",
            ErrorCode::NullInJoinKey => "null_in_join_key",
            ErrorCode::DisconnectedJoinGraph => "disconnected_join_graph",
            ErrorCode::NoCommonAttribute => "no_common_attribute",
            ErrorCode::VariableNotBound => "variable_not_bound",
            ErrorCode::DuplicateVariable => "duplicate_variable",
            ErrorCode::BadLimit => "bad_limit",
            ErrorCode::InvalidCursor => "invalid_cursor",
            ErrorCode::MissingRelation => "missing_relation",
            ErrorCode::RelationNameMismatch => "relation_name_mismatch",
            ErrorCode::EncodeFailed => "encode_failed",
            ErrorCode::InternalInvariant => "internal_invariant",
        }
    }

    pub fn category(self) -> ErrorCategory {
        match self {
            ErrorCode::EmptyRelationList
            | ErrorCode::TooManyRelations
            | ErrorCode::EmptyRelation
            | ErrorCode::EmptySchema
            | ErrorCode::UnknownColumn
            | ErrorCode::DuplicateColumn
            | ErrorCode::ColumnArityMismatch
            | ErrorCode::RowArityMismatch
            | ErrorCode::TypeMismatch
            | ErrorCode::UnsupportedJoinType
            | ErrorCode::NullInJoinKey
            | ErrorCode::DisconnectedJoinGraph
            | ErrorCode::NoCommonAttribute
            | ErrorCode::VariableNotBound
            | ErrorCode::DuplicateVariable
            | ErrorCode::BadLimit
            | ErrorCode::InvalidCursor
            | ErrorCode::MissingRelation
            | ErrorCode::RelationNameMismatch => ErrorCategory::Validation,
            ErrorCode::EncodeFailed => ErrorCategory::Serde,
            ErrorCode::InternalInvariant => ErrorCategory::Internal,
        }
    }
}

#[derive(Debug, Clone)]
pub struct JoinError {
    pub code: ErrorCode,
    pub message: String,
}

impl JoinError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    pub fn category(&self) -> ErrorCategory {
        self.code.category()
    }
}

impl fmt::Display for JoinError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.code.as_str(), self.message)
    }
}

impl std::error::Error for JoinError {}

pub type JoinResult<T> = Result<T, JoinError>;
