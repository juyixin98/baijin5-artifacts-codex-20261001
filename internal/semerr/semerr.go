// Package semerr defines the single error contract shared by the frontend,
// IR transform, runtime interpreters and the differential harness.
//
// Every failure that crosses a module boundary is tagged with one of four
// kinds so that tests can assert the failure category rather than a message:
//
//   - KindInput    : invalid source (lexing/parsing/static validation)
//   - KindConflict : generator state conflict (resume a running generator,
//                    close a running generator, send into a newborn one)
//   - KindResource : bounded resource exhausted (fuel / recursion depth)
//   - KindCompute  : a language-level exception raised by the program,
//                    or forced iteration end
package semerr

import "fmt"

type Kind string

const (
	KindInput    Kind = "INPUT"
	KindConflict Kind = "CONFLICT"
	KindResource Kind = "RESOURCE"
	KindCompute  Kind = "COMPUTE"
)

// Code is a stable, machine-readable failure identifier.
type Code string

const (
	CodeNone Code = ""

	// Input errors.
	CodeLex         Code = "LEX_ERROR"
	CodeParse       Code = "PARSE_ERROR"
	CodeStatic      Code = "STATIC_ERROR"
	CodeBadRequest  Code = "BAD_REQUEST"
	CodeIRInvariant Code = "IR_INVARIANT"

	// State conflicts.
	CodeResumeRunning Code = "RESUME_RUNNING"
	CodeCloseRunning  Code = "CLOSE_RUNNING"
	CodeSendNewborn   Code = "SEND_NEWBORN"
	CodeYieldInClose  Code = "YIELD_IN_CLOSE"

	// Resource exhaustion.
	CodeOutOfFuel       Code = "OUT_OF_FUEL"
	CodeCallDepth       Code = "CALL_DEPTH_EXCEEDED"
	CodeGeneratorLimit  Code = "GENERATOR_LIMIT_EXCEEDED"

	// Compute failures.
	CodeUserRaised  Code = "USER_RAISED"
	CodeStopIter    Code = "STOP_ITERATION"
	CodeBuiltinFail Code = "BUILTIN_FAIL"
)

// Error is the typed error exchanged between modules.
type Error struct {
	Kind Kind
	Code Code
	Msg  string
	// ExName/ExMessage describe the language-level exception for KindCompute.
	ExName    string
	ExMessage string
}

func (e *Error) Error() string {
	if e.Kind == KindCompute && e.Code == CodeUserRaised {
		return fmt.Sprintf("%s: %s", e.ExName, e.ExMessage)
	}
	return fmt.Sprintf("%s[%s]: %s", e.Kind, e.Code, e.Msg)
}

func New(kind Kind, code Code, format string, args ...any) *Error {
	return &Error{Kind: kind, Code: code, Msg: fmt.Sprintf(format, args...)}
}

func Input(code Code, format string, args ...any) *Error {
	return New(KindInput, code, format, args...)
}

func Conflict(code Code, format string, args ...any) *Error {
	return New(KindConflict, code, format, args...)
}

func Resource(code Code, format string, args ...any) *Error {
	return New(KindResource, code, format, args...)
}

func Compute(code Code, format string, args ...any) *Error {
	return New(KindCompute, code, format, args...)
}

// As extracts a *semerr.Error from any error.
func As(err error) (*Error, bool) {
	if err == nil {
		return nil, false
	}
	if se, ok := err.(*Error); ok {
		return se, true
	}
	return nil, false
}

// Is reports whether err has the given kind.
func Is(err error, kind Kind) bool {
	se, ok := As(err)
	return ok && se.Kind == kind
}
