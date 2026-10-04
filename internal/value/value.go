// Package value defines the runtime value model shared by both execution
// engines (the direct tree-walking interpreter and the FSM interpreter),
// plus the generator handle abstraction used by the differential harness.
package value

import (
	"fmt"
	"strconv"
)

type Tag int

const (
	Null Tag = iota
	Bool
	Int
	Str
	Closure
	Generator
	Exception
)

// Gen is the engine-independent interface of a live generator instance.
// Both execution engines provide an implementation of Gen so that the
// harness can drive them with identical actions.
type Gen interface {
	// GenName returns the generator function name (for traces/errors).
	GenName() string
	// GenID returns a process-unique numeric id (for traces).
	GenID() int64
	// Status reports the current lifecycle state.
	Status() GenStatus
	// Next resumes with no injected value/exception.
	Next() Result
	// Send resumes injecting a value (illegal while newborn -> CONFLICT).
	Send(v Value) Result
	// Throw resumes injecting an exception.
	Throw(ex Value) Result
	// Close closes the generator: no-op when already closed/finished,
	// runs applicable cleanups exactly once when suspended/newborn.
	Close() Result
}

type GenStatus string

const (
	StatusNewborn   GenStatus = "newborn"
	StatusRunning   GenStatus = "running"
	StatusSuspended GenStatus = "suspended"
	StatusFinished  GenStatus = "finished"
	StatusClosed    GenStatus = "closed"
)

// Result is the uniform outcome of a generator control action.
type Result struct {
	State GenState // yielded / finished / closed
	Value Value    // yielded value or return value
	// ErrKind/ErrCode classify a failed action; empty when OK.
	ErrKind string
	ErrCode string
	ErrMsg  string
	// ExName/ExMessage describe user-level exceptions.
	ExName    string
	ExMessage string
}

type GenState string

const (
	StateYielded  GenState = "yielded"
	StateFinished GenState = "finished"
	StateClosed   GenState = "closed"
	StateError    GenState = "error"
)

// Value is the boxed runtime value.
type Value struct {
	Tag Tag
	B   bool
	I   int64
	S   string
	// ClosureFn carries a function name understood by both engines.
	ClosureFn string
	Gen       Gen
	// Exception identity (Tag == Exception).
	ExName    string
	ExMessage string
}

func NullV() Value                   { return Value{Tag: Null} }
func BoolV(b bool) Value             { return Value{Tag: Bool, B: b} }
func IntV(i int64) Value             { return Value{Tag: Int, I: i} }
func StrV(s string) Value            { return Value{Tag: Str, S: s} }
func FnV(name string) Value          { return Value{Tag: Closure, ClosureFn: name} }
func GenV(g Gen) Value               { return Value{Tag: Generator, Gen: g} }
func ExV(name, message string) Value { return Value{Tag: Exception, ExName: name, ExMessage: message} }

func (v Value) IsTruthy() bool {
	switch v.Tag {
	case Null:
		return false
	case Bool:
		return v.B
	case Int:
		return v.I != 0
	case Str:
		return v.S != ""
	default:
		return true
	}
}

func (v Value) Display() string {
	switch v.Tag {
	case Null:
		return "null"
	case Bool:
		if v.B {
			return "true"
		}
		return "false"
	case Int:
		return strconv.FormatInt(v.I, 10)
	case Str:
		return v.S
	case Closure:
		return "<fn " + v.ClosureFn + ">"
	case Generator:
		if v.Gen == nil {
			return "<generator>"
		}
		return fmt.Sprintf("<generator %s#%d %s>", v.Gen.GenName(), v.Gen.GenID(), v.Gen.Status())
	case Exception:
		return v.ExName + ": " + v.ExMessage
	default:
		return "<?>"
	}
}

// Equal implements language-level equality.
func Equal(a, b Value) bool {
	if a.Tag != b.Tag {
		return false
	}
	switch a.Tag {
	case Null:
		return true
	case Bool:
		return a.B == b.B
	case Int:
		return a.I == b.I
	case Str, Exception:
		return a.S == b.S
	case Closure:
		return a.ClosureFn == b.ClosureFn
	case Generator:
		return a.Gen == b.Gen
	}
	return false
}
