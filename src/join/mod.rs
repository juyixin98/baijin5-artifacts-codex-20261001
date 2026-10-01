//! Join operators: the Leapfrog Triejoin engine, resume tokens, and an
//! independent naive reference used only by tests.
pub mod cursor;
pub mod engine;
#[doc(hidden)]
pub mod naive;

pub use cursor::{decode_after, encode_after};
pub use engine::{execute, EmittedRow, ExecOutput};
