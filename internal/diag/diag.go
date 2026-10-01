// Package diag defines the cross-module error contract for ScopeLang.
//
// Every failure that crosses a package boundary is an *Error carrying:
//   - Category: one of the four required user-visible classes
//     (InputError, StateConflict, ResourceExhausted, ComputationFailure)
//     or Internal (a defect in the toolchain itself).
//   - Phase:    the pipeline stage that produced the failure.
//   - Point:    the stable injection point (init@N/fail@N/close@N), when known.
//   - Instance: the repeat-iteration instance suffix, when known.
//
// Suppressed failures (errors raised while another unwind is already in
// flight) are attached to the primary error and are still observable.
package diag

import "fmt"

type Category string

const (
	Input             Category = "InputError"
	StateConflict     Category = "StateConflict"
	ResourceExhausted Category = "ResourceExhausted"
	Computation       Category = "ComputationFailure"
	Internal          Category = "Internal"
)

type Phase string

const (
	PhaseFrontend Phase = "frontend"
	PhaseLower    Phase = "lower"
	PhaseVerify   Phase = "verify"
	PhaseRuntime  Phase = "runtime"
	PhaseConfig   Phase = "config"
	PhaseOracle   Phase = "oracle"
)

type Error struct {
	Category   Category `json:"category"`
	Code       string   `json:"code"`
	Message    string   `json:"message"`
	Phase      Phase    `json:"phase,omitempty"`
	Point      string   `json:"point,omitempty"`
	Instance   int      `json:"instance,omitempty"`
	Suppressed []*Error `json:"suppressed,omitempty"`
}

func (e *Error) Error() string {
	loc := ""
	if e.Point != "" {
		loc = " at " + e.Point
		if e.Instance > 0 {
			loc += fmt.Sprintf("#%d", e.Instance)
		}
	}
	return fmt.Sprintf("%s[%s]%s: %s", e.Category, e.Code, loc, e.Message)
}

func New(cat Category, code, msg string) *Error {
	return &Error{Category: cat, Code: code, Message: msg}
}

// With annotates an error with phase/point/instance metadata. Nil passes through.
func (e *Error) With(phase Phase, point string, instance int) *Error {
	if e == nil {
		return nil
	}
	e.Phase = phase
	if point != "" {
		e.Point = point
	}
	if instance > 0 {
		e.Instance = instance
	}
	return e
}

// Attach records a secondary error observed during unwinding. The primary
// error never changes identity: the first error wins for the whole run.
func (e *Error) Attach(s *Error) *Error {
	if e == nil {
		return s
	}
	if s != nil && s != e {
		e.Suppressed = append(e.Suppressed, s)
	}
	return e
}

// AsError extracts a *Error from any error.
func AsError(err error) *Error {
	if err == nil {
		return nil
	}
	if e, ok := err.(*Error); ok {
		return e
	}
	return &Error{Category: Internal, Code: "unknown", Message: err.Error()}
}
