//! Result sink encoding: typed batches to base64 Arrow IPC streams.

use base64::Engine;

use crate::batch::TypedBatch;
use crate::error::{EngineError, EngineResult};
use arrow2::io::ipc::write::{StreamWriter, WriteOptions};

/// Serialize a batch as an Arrow IPC streaming envelope, base64-encoded.
pub(crate) fn encode_ipc(batch: &TypedBatch) -> EngineResult<String> {
    let schema = batch.arrow_schema();
    let chunk = batch.to_arrow_chunk()?;
    let mut buf = Vec::new();
    {
        let mut writer = StreamWriter::new(&mut buf, WriteOptions { compression: None });
        writer
            .start(&schema, None)
            .map_err(|e| EngineError::internal(format!("ipc start failed: {e}")))?;
        writer
            .write(&chunk, None)
            .map_err(|e| EngineError::internal(format!("ipc write failed: {e}")))?;
        writer
            .finish()
            .map_err(|e| EngineError::internal(format!("ipc finish failed: {e}")))?;
    }
    Ok(base64::engine::general_purpose::STANDARD.encode(buf))
}
