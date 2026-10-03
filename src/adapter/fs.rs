//! Real local-filesystem adapter used by the demo binary.
//!
//! Each operation runs on a short-lived OS thread and reports back through a
//! channel. Filesystem IO cannot be cancelled on most platforms, so
//! [`FsAdapter::cancel`] honestly returns [`CancelAck::Unsupported`]: the
//! runtime accepts the cancel *request*, but the completion will still
//! arrive and the record will finalize as `Success`/`Failed`. This is the
//! real-world illustration of "cancel accepted" != "IO did not happen".

use super::{AdapterEvent, CancelAck, IoAdapter};
use crate::model::{Generation, OpKind};
use std::fs;
use std::io::Write;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::thread;

pub struct FsAdapter {
    tx: Sender<AdapterEvent>,
    rx: Receiver<AdapterEvent>,
}

impl Default for FsAdapter {
    fn default() -> Self {
        Self::new()
    }
}

impl FsAdapter {
    pub fn new() -> Self {
        let (tx, rx) = channel();
        Self { tx, rx }
    }
}

fn run_op(op: &OpKind) -> Result<usize, String> {
    match op {
        OpKind::Nop => Ok(0),
        OpKind::ReadFile { path } => fs::read(path)
            .map(|buf| buf.len())
            .map_err(|e| format!("read {}: {e}", path.display())),
        OpKind::WriteFile { path, len } => {
            let buf = vec![0u8; *len];
            let mut file = fs::File::create(path)
                .map_err(|e| format!("create {}: {e}", path.display()))?;
            file.write_all(&buf)
                .map_err(|e| format!("write {}: {e}", path.display()))?;
            Ok(*len)
        }
    }
}

impl IoAdapter for FsAdapter {
    fn start(&mut self, slot: u16, generation: Generation, op: &OpKind) {
        let tx = self.tx.clone();
        let op = op.clone();
        thread::spawn(move || {
            let result = run_op(&op);
            // The receiver may be gone during shutdown; losing the event is
            // acceptable there and harmless in tests.
            let _ = tx.send(AdapterEvent::Completed {
                slot,
                generation,
                result,
            });
        });
    }

    fn cancel(&mut self, _slot: u16, _generation: Generation) -> CancelAck {
        CancelAck::Unsupported
    }

    fn abort(&mut self, _slot: u16, _generation: Generation) {
        // Nothing to clean up: the worker thread finishes on its own and its
        // late event will be rejected by the core as stale.
    }

    fn poll(&mut self) -> Vec<AdapterEvent> {
        let mut events = Vec::new();
        while let Ok(event) = self.rx.try_recv() {
            events.push(event);
        }
        events
    }
}
