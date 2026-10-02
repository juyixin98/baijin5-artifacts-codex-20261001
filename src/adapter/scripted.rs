//! Controllable synthetic adapter.
//!
//! Behaviours are consumed from a script in `start` order; once the script
//! is exhausted the default behaviour applies. The adapter is deterministic
//! in `(script, time)` and supports fault injection (`inject`) so tests can
//! replay stale or duplicate completions exactly as a misbehaving device
//! would produce them.

use std::collections::VecDeque;

use crate::model::{FailureKind, IoOp, UserData};

use super::{AdapterCompletion, CancelAck, IoAdapter, IoResult};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Behavior {
    /// Completes successfully after `delay_ms`, regardless of cancel
    /// requests (the device already committed the IO).
    Succeed { delay_ms: u64, bytes: u64 },
    /// Fails after `delay_ms`.
    Fail { delay_ms: u64 },
    /// Never completes on its own; a cancel request makes it complete with
    /// `Cancelled { io_performed }`.
    HangUntilCancel { performed_on_cancel: bool },
    /// Never completes, even when cancelled (lost request). Drives the
    /// timeout path; the buffer stays leased forever.
    Never,
    /// Misbehaving device: emits the same completion twice.
    DoubleComplete { delay_ms: u64, bytes: u64 },
}

#[derive(Debug)]
struct InFlight {
    token: UserData,
    behavior: Behavior,
    started_at: u64,
    cancel_requested: bool,
    emitted: bool,
}

#[derive(Debug)]
pub struct ScriptedAdapter {
    script: VecDeque<Behavior>,
    default_behavior: Behavior,
    demo_paths: bool,
    demo_delay_ms: u64,
    in_flight: Vec<InFlight>,
    injected: Vec<AdapterCompletion>,
    /// Recorded for test assertions: every cancel the device received.
    pub cancel_acks: Vec<(UserData, CancelAck)>,
}

impl ScriptedAdapter {
    pub fn new(default_behavior: Behavior) -> Self {
        Self {
            script: VecDeque::new(),
            default_behavior,
            demo_paths: false,
            demo_delay_ms: 0,
            in_flight: Vec::new(),
            injected: Vec::new(),
            cancel_acks: Vec::new(),
        }
    }

    pub fn with_script(script: Vec<Behavior>, default_behavior: Behavior) -> Self {
        let mut adapter = Self::new(default_behavior);
        adapter.script = script.into();
        adapter
    }

    /// Server mode: behaviour is chosen per operation from the path.
    /// `demo:hang`, `demo:hang-partial`, `demo:never`, `demo:fail`,
    /// `demo:slow`, `demo:flaky-cancel`, `demo:double`; anything else
    /// succeeds after `default_delay_ms`.
    pub fn demo_server(default_delay_ms: u64) -> Self {
        let mut adapter = Self::new(Behavior::Succeed {
            delay_ms: default_delay_ms,
            bytes: 512,
        });
        adapter.demo_paths = true;
        adapter.demo_delay_ms = default_delay_ms;
        adapter
    }

    fn behavior_for(&mut self, op: &IoOp) -> Behavior {
        if let Some(b) = self.script.pop_front() {
            return b;
        }
        if self.demo_paths {
            let path = match op {
                IoOp::Read { path } => path.as_str(),
                IoOp::Write { path, .. } => path.as_str(),
            };
            let d = self.demo_delay_ms;
            if let Some(demo) = path.strip_prefix("demo:") {
                return match demo {
                    "hang" => Behavior::HangUntilCancel {
                        performed_on_cancel: false,
                    },
                    "hang-partial" => Behavior::HangUntilCancel {
                        performed_on_cancel: true,
                    },
                    "never" => Behavior::Never,
                    "fail" => Behavior::Fail { delay_ms: d },
                    "slow" => Behavior::Succeed {
                        delay_ms: 10 * d,
                        bytes: 512,
                    },
                    "flaky-cancel" => Behavior::Succeed {
                        delay_ms: 3 * d,
                        bytes: 512,
                    },
                    "double" => Behavior::DoubleComplete {
                        delay_ms: d,
                        bytes: 512,
                    },
                    _ => self.default_behavior,
                };
            }
        }
        self.default_behavior
    }

    /// Fault injection: queue a raw completion as if the device emitted it.
    /// Used to replay stale-generation or duplicate completions.
    pub fn inject(&mut self, completion: AdapterCompletion) {
        self.injected.push(completion);
    }
}

impl IoAdapter for ScriptedAdapter {
    fn start(&mut self, token: UserData, op: &IoOp, now_ms: u64) {
        let behavior = self.behavior_for(op);
        self.in_flight.push(InFlight {
            token,
            behavior,
            started_at: now_ms,
            cancel_requested: false,
            emitted: false,
        });
    }

    fn cancel(&mut self, token: UserData) -> CancelAck {
        let ack = match self.in_flight.iter_mut().find(|f| f.token == token) {
            Some(f) => {
                f.cancel_requested = true;
                CancelAck::WillCancel
            }
            None => CancelAck::NotFound,
        };
        self.cancel_acks.push((token, ack));
        ack
    }

    fn poll(&mut self, now_ms: u64) -> Vec<AdapterCompletion> {
        let mut out = std::mem::take(&mut self.injected);
        for f in &mut self.in_flight {
            if f.emitted {
                continue;
            }
            let due = |delay: u64| now_ms >= f.started_at.saturating_add(delay);
            let completion = match f.behavior {
                Behavior::Succeed { delay_ms, bytes } if due(delay_ms) => {
                    Some(IoResult::Success { bytes })
                }
                Behavior::Fail { delay_ms } if due(delay_ms) => {
                    Some(IoResult::Failed {
                        kind: FailureKind::Io,
                    })
                }
                Behavior::HangUntilCancel {
                    performed_on_cancel,
                } if f.cancel_requested => Some(IoResult::Cancelled {
                    io_performed: performed_on_cancel,
                }),
                Behavior::DoubleComplete { delay_ms, bytes } if due(delay_ms) => {
                    out.push(AdapterCompletion {
                        token: f.token,
                        result: IoResult::Success { bytes },
                    });
                    Some(IoResult::Success { bytes })
                }
                _ => None,
            };
            if let Some(result) = completion {
                f.emitted = true;
                out.push(AdapterCompletion {
                    token: f.token,
                    result,
                });
            }
        }
        self.in_flight.retain(|f| !f.emitted);
        out
    }

    fn in_flight(&self) -> usize {
        self.in_flight.len()
    }
}
