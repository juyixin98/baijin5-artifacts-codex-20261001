// Package difftest drives the FSM engine and the reference interpreter with
// identical action scripts and compares every observable result. It is the
// semantic differential: a failing comparison pinpoints the run number, the
// action, each engine's state, and the reason for the mismatch.
package difftest

import (
	"fmt"

	"genfsm/internal/value"
)

// ActionKind enumerates driver operations.
type ActionKind string

const (
	ActNew    ActionKind = "new"    // create a newborn generator by name
	ActNext   ActionKind = "next"
	ActSend   ActionKind = "send"
	ActSendGen ActionKind = "sendgen" // send a previously created handle
	ActThrow  ActionKind = "throw"
	ActClose  ActionKind = "close"
	ActStatus ActionKind = "status"
)

// Action is one scripted driver operation.
type Action struct {
	Kind ActionKind
	// Target selects the handle slot returned by a prior "new".
	Target int
	// Args.
	GenName  string
	Args     []value.Value
	SendVal  value.Value
	SendHandle int // for ActSendGen: target slot whose handle is sent
	ExName   string
	ExMsg    string
	// Comment annotates the run in the replay log.
	Comment string
}

// Observation is the comparable outcome of an action on one engine.
type Observation struct {
	State     string // yielded/finished/closed/error/conflict/status
	Val       string // normalized display
	ValTag    string
	ErrKind   string
	ErrCode   string
	ExName    string
	ExMessage string
	Status    string // for ActStatus
}

func observeResult(r value.Result) Observation {
	o := Observation{
		State:     string(r.State),
		ErrKind:   r.ErrKind,
		ErrCode:   r.ErrCode,
		ExName:    r.ExName,
		ExMessage: r.ExMessage,
	}
	if r.State == value.StateYielded || r.State == value.StateFinished {
		o.Val = r.Value.Display()
		o.ValTag = tagName(r.Value.Tag)
	}
	return o
}

func tagName(t value.Tag) string {
	switch t {
	case value.Null:
		return "null"
	case value.Bool:
		return "bool"
	case value.Int:
		return "int"
	case value.Str:
		return "str"
	case value.Closure:
		return "fn"
	case value.Generator:
		return "generator"
	case value.Exception:
		return "exception"
	}
	return fmt.Sprintf("tag%d", int(t))
}
