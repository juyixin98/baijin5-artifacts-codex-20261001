package runtime

// State is the externally observable lifecycle state of a generator.
type State int

const (
	// Created: constructed but never resumed.
	Created State = iota
	// Running: currently executing a Next/Throw/Close request.
	Running
	// Suspended: parked at a yield; Next/Throw/Close are valid.
	Suspended
	// Done: finished normally or via return; exhausted.
	Done
	// Failed: terminated by an uncaught exception or resource exhaustion.
	Failed
	// Closed: exactly one Close has completed cleanup; closed is terminal.
	Closed
)

func (s State) String() string {
	switch s {
	case Created:
		return "created"
	case Running:
		return "running"
	case Suspended:
		return "suspended"
	case Done:
		return "done"
	case Failed:
		return "failed"
	case Closed:
		return "closed"
	}
	return "unknown"
}

// OutcomeKind classifies the result of a single driver operation.
type OutcomeKind int

const (
	// OutYielded: the operation produced one value (Next suspended at yield).
	OutYielded OutcomeKind = iota
	// OutExhausted: a normally-finished generator produced no value.
	OutExhausted
	// OutClosedOK: a Close request completed all applicable cleanups.
	OutClosedOK
	// OutFailed: the request terminated with an error (field Err).
	OutFailed
)

func (k OutcomeKind) String() string {

	switch k {
	case OutYielded:
		return "yielded"
	case OutExhausted:
		return "exhausted"
	case OutClosedOK:
		return "closed_ok"
	case OutFailed:
		return "failed"
	}
	return "?"
}

// Outcome is the result of one Next/Throw/Close request.
type Outcome struct {
	Kind  OutcomeKind `json:"kind"`
	Value *Value      `json:"value,omitempty"`
	// State is the generator state observed after the request settled.
	State State `json:"state"`
	// Logs are the ordered log() side effects produced during this request.
	Logs []string `json:"logs,omitempty"`
	// Err holds a stable error code for Failed outcomes; empty otherwise.
	ErrCode string `json:"err_code,omitempty"`
	ErrMsg  string `json:"err_msg,omitempty"`
}

// Generator is the common contract implemented by both the explicit
// state-machine machine and the direct (goroutine) interpreter.
type Generator interface {
	Name() string
	State() State
	// Next resumes a Created/Suspended generator.
	Next() Outcome
	// Throw injects an exception at the current suspension point (or before
	// start when Created). Only valid in Created/Suspended.
	Throw(v Value) Outcome
	// Close runs pending cleanups exactly once. Only valid in Created or
	// Suspended; closing a terminal generator is a state conflict.
	Close() Outcome
	// Snapshot returns a debug view for replay/differential logs.
	Snapshot() Snapshot
}

// Snapshot is the serializable intermediate state used by the differential
// harness to localize a divergence.
type Snapshot struct {
	Name     string           `json:"name"`
	State    string           `json:"state"`
	Block    string           `json:"block,omitempty"`
	BlockIdx int              `json:"block_idx,omitempty"`
	Locals   map[string]Value `json:"locals"`
	Handlers int              `json:"handlers"`
	Steps    int64            `json:"steps"`
}
