//! Budgeted resolution-based Craig interpolation engine.

pub mod budget;
pub mod cnf;
pub mod engine;
pub mod events;
pub mod interpolant;
pub mod witness;

pub use budget::{BudgetCounter, Charge, ResolutionBudget};
pub use cnf::{desugar, to_cnf_direct, to_cnf_tseitin, CnfFormula, TseitinEncoding};
pub use engine::{
    EngineInput, EngineVerdict, InterpolationEngine, JointlySatisfiable, Proved, UnknownReason,
};
pub use events::{EngineEvent, EventLevel, EventLog};
