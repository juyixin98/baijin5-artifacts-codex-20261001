//! Resource limits guarding the checker against pathological inputs.
//!
//! Exceeding any of these is reported as [`ErrorCategory::ResourceExhausted`]
//! and is distinct from malformed input and from arithmetic overflow.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    pub max_symbols: usize,
    pub max_rules: usize,
    pub max_arity: usize,
    /// Maximum number of nodes of any single term.
    pub max_term_nodes: usize,
    /// Maximum nesting depth of any single term (also bounds recursion).
    pub max_term_depth: usize,
    /// Upper bound for every coefficient and constant appearing during
    /// polynomial evaluation (certificate constants/coefficients included).
    pub max_coefficient: u64,
}

impl Default for Limits {
    fn default() -> Self {
        Limits {
            max_symbols: 1024,
            max_rules: 4096,
            max_arity: 64,
            max_term_nodes: 100_000,
            max_term_depth: 512,
            max_coefficient: 1_000_000_000,
        }
    }
}
