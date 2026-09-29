//! Query operators: the IEJoin core, the independent nested-loop reference,
//! the bitmap primitive and the query plan types.

pub mod bitmap;
pub mod iejoin;
pub mod nested_loop;
pub mod plan;

pub use iejoin::{join_full, Checkpoint, JoinPage, OutputPair, PreparedJoin};
pub use nested_loop::nested_loop;
pub use plan::{CanonicalPredicate, Comparator, JoinPlan, Predicate};
