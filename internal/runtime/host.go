package runtime

import (
	"fmt"
	"sync"

	"scopelang/internal/diag"
)

// EventKind enumerates observable trace events.
type EventKind string

const (
	EvAcquireStart   EventKind = "acquire_start"
	EvAcquireSuccess EventKind = "acquire_success"
	EvReleaseStart   EventKind = "release_start"
	EvReleaseDone    EventKind = "release_done"
	EvEmit           EventKind = "emit"
)

// Event is one observable step. Instance is the 1-based repeat iteration for
// in-loop resources and 0 otherwise.
type Event struct {
	Seq      int       `json:"seq"`
	Kind     EventKind `json:"kind"`
	Point    string    `json:"point,omitempty"`
	Name     string    `json:"name,omitempty"`
	Expr     string    `json:"expr,omitempty"`
	Text     string    `json:"text,omitempty"`
	Instance int       `json:"instance,omitempty"`
}

// Host is the synthetic resource manager used identically by both execution
// engines. A fresh Host per run guarantees replay determinism.
type Host struct {
	mu       sync.Mutex
	trace    []Event
	live     map[string]bool // key name#instance
	poolUsed int
	poolMax  int
	spec     *RunSpec
	seq      int
}

const defaultPoolMax = 64

func NewHost(spec *RunSpec) *Host {
	return &Host{live: map[string]bool{}, poolMax: defaultPoolMax, spec: spec}
}

func (h *Host) Trace() []Event {
	out := make([]Event, len(h.trace))
	copy(out, h.trace)
	return out
}

func (h *Host) record(kind EventKind, point, name, expr, text string, instance int) Event {
	h.seq++
	ev := Event{Seq: h.seq, Kind: kind, Point: point, Name: name, Expr: expr,
		Text: text, Instance: instance}
	h.trace = append(h.trace, ev)
	return ev
}

func resourceKey(name string, instance int) string {
	if instance > 0 {
		return fmt.Sprintf("%s#%d", name, instance)
	}
	return name
}

func (h *Host) lookup(point string, instance int) *Injection {
	var generic *Injection
	for i := range h.spec.Injections {
		inj := &h.spec.Injections[i]
		if inj.Point != point {
			continue
		}
		if inj.Instance == instance && instance > 0 {
			return inj
		}
		if inj.Instance == 0 {
			generic = inj
		}
	}
	return generic
}

func injectedError(inj *Injection, point string, instance int) *diag.Error {
	cat := inj.Category
	code := inj.Code
	if code == "" {
		code = defaultCode(cat)
	}
	msg := inj.Message
	if msg == "" {
		msg = fmt.Sprintf("injected %s at %s", cat, point)
	}
	return diag.New(cat, code, msg).With(diag.PhaseRuntime, point, instance)
}

func defaultCode(c diag.Category) string {
	switch c {
	case diag.Input:
		return "INJECTED_INPUT"
	case diag.StateConflict:
		return "INJECTED_STATE_CONFLICT"
	case diag.ResourceExhausted:
		return "INJECTED_POOL_EXHAUSTED"
	default:
		return "INJECTED_COMPUTATION"
	}
}

// Acquire models initialization: start event, failure injection lookup,
// capacity/state checks, success bookkeeping. It does not set any VM guard;
// the caller (tree interpreter / VM) owns that.
func (h *Host) Acquire(point, name, expr string, instance int) *diag.Error {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.record(EvAcquireStart, point, name, expr, "", instance)
	if inj := h.lookup(point, instance); inj != nil {
		return injectedError(inj, point, instance)
	}
	key := resourceKey(name, instance)
	if h.live[key] {
		return diag.New(diag.StateConflict, "RESOURCE_LIVE",
			fmt.Sprintf("resource %s is already live", key)).
			With(diag.PhaseRuntime, point, instance)
	}
	if h.poolUsed >= h.poolMax {
		return diag.New(diag.ResourceExhausted, "POOL_EXHAUSTED",
			fmt.Sprintf("synthetic resource pool exhausted (%d)", h.poolMax)).
			With(diag.PhaseRuntime, point, instance)
	}
	h.poolUsed++
	h.live[key] = true
	h.record(EvAcquireSuccess, point, name, expr, "", instance)
	return nil
}

// Release models destruction. A release of a non-live key is a host-side
// state conflict; the interpreter guards prevent it in correct programs.
func (h *Host) Release(point, name string, instance int) *diag.Error {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.record(EvReleaseStart, point, name, "", "", instance)
	key := resourceKey(name, instance)
	if !h.live[key] {
		return diag.New(diag.StateConflict, "RESOURCE_NOT_LIVE",
			fmt.Sprintf("release of non-live resource %s", key)).
			With(diag.PhaseRuntime, point, instance)
	}
	delete(h.live, key)
	h.poolUsed--
	h.record(EvReleaseDone, point, name, "", "", instance)
	return nil
}

// CleanupError applies a cleanup-body explicit fail (close@N) or returns nil.
func (h *Host) CleanupError(point string, instance int) *diag.Error {
	if inj := h.lookup(point, instance); inj != nil {
		return injectedError(inj, point, instance)
	}
	return diag.New(diag.Computation, "CLEANUP_FAILED",
		fmt.Sprintf("cleanup body failed at %s", point)).
		With(diag.PhaseRuntime, point, instance)
}

// ExplicitFail reports an unconditional fail@N: injected category wins,
// otherwise ComputationFailure.
func (h *Host) ExplicitFail(point string, instance int) *diag.Error {
	if inj := h.lookup(point, instance); inj != nil {
		return injectedError(inj, point, instance)
	}
	return diag.New(diag.Computation, "EXPLICIT_FAIL",
		fmt.Sprintf("explicit fail at %s", point)).
		With(diag.PhaseRuntime, point, instance)
}

func (h *Host) Emit(text string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.record(EvEmit, "", "", "", text, 0)
}
