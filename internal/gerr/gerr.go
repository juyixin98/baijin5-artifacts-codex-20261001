// Package gerr defines the cross-module error contract for the generator
// state-machine project.
//
// Every failure reported across module boundaries (frontend -> compiler ->
// runtime -> differential harness) carries a stable Code so that callers and
// tests can distinguish the four failure families demanded by the spec:
//
//   - input errors       (E* codes): malformed source or invalid requests
//   - state conflicts    (S* codes): illegal operation for the current state
//   - resource exhaustion (R* codes): step budget exceeded
//   - computation failure (C* codes): user program raised/aborted
package gerr

import (
	"fmt"
)

// Kind groups error codes into the four failure families.
type Kind string

const (
	KindInput       Kind = "input_error"
	KindState       Kind = "state_conflict"
	KindResource    Kind = "resource_exhausted"
	KindComputation Kind = "computation_failure"
)

// Code is a stable, machine-readable error identifier.
type Code string

const (
	// Input errors.
	EParse        Code = "parse_error"
	EValidate     Code = "validation_error"
	EYieldFinally Code = "yield_in_finally"
	EUnknownGen   Code = "unknown_generator"
	EDupGen       Code = "duplicate_generator"
	EBadRequest   Code = "bad_request"

	// State conflicts.
	SNotSuspended Code = "not_suspended"
	SReentry      Code = "generator_running"
	SAlreadyDone  Code = "generator_closed"

	// Resource exhaustion.
	RStepBudget Code = "step_budget_exceeded"

	// Computation failures.
	CUserRaised   Code = "user_exception"
	CDivZero      Code = "division_by_zero"
	CTypeMismatch Code = "type_mismatch"
	CYieldClosing Code = "yield_during_close"
	CUncaughtExit Code = "uncaught_exit"
)

func kindOf(c Code) Kind {
	switch c {
	case EParse, EValidate, EYieldFinally, EUnknownGen, EDupGen, EBadRequest:
		return KindInput
	case SNotSuspended, SReentry, SAlreadyDone:
		return KindState
	case RStepBudget:
		return KindResource
	default:
		return KindComputation
	}
}

// Error is the structured error shared by every module.
type Error struct {
	Code    Code           `json:"code"`
	Kind    Kind           `json:"kind"`
	Message string         `json:"message"`
	Line    int            `json:"line,omitempty"`
	Column  int            `json:"column,omitempty"`
	Payload map[string]any `json:"payload,omitempty"`
}

func (e *Error) Error() string {
	pos := ""
	if e.Line > 0 {
		pos = fmt.Sprintf(" (line %d col %d)", e.Line, e.Column)
	}
	return fmt.Sprintf("%s: %s%s", e.Code, e.Message, pos)
}

// New builds a structured error for the given code.
func New(code Code, format string, args ...any) *Error {
	return &Error{Code: code, Kind: kindOf(code), Message: fmt.Sprintf(format, args...)}
}

// AtPos attaches a source position to the error.
func (e *Error) AtPos(line, col int) *Error {
	e.Line, e.Column = line, col
	return e
}

// WithPayload attaches structured detail.
func (e *Error) WithPayload(k string, v any) *Error {
	if e.Payload == nil {
		e.Payload = map[string]any{}
	}
	e.Payload[k] = v
	return e
}

// As extracts a *gerr.Error from any error, reporting whether it is one.
func As(err error) (*Error, bool) {
	if err == nil {
		return nil, false
	}
	if ge, ok := err.(*Error); ok {
		return ge, true
	}
	return nil, false
}

// IsCode reports whether err carries the given code.
func IsCode(err error, code Code) bool {
	if ge, ok := As(err); ok {
		return ge.Code == code
	}
	return false
}
