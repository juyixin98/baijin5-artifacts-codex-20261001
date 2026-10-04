package difftest

import (
	"fmt"
	"strings"

	"genfsm/internal/engine"
	"genfsm/internal/value"
)

// Event is one structured log record sufficient to replay a problem.
type Event struct {
	Run      int      `json:"run"`
	Action   string   `json:"action"`
	Target   int      `json:"target,omitempty"`
	Comment  string   `json:"comment,omitempty"`
	FSM      Observation `json:"fsm"`
	Ref      Observation `json:"ref"`
	Match    bool     `json:"match"`
	Reason   string   `json:"reason,omitempty"`
	// Intermediate state captured on mismatch/always at key points.
	FSMStatus string `json:"fsm_status,omitempty"`
	RefStatus string `json:"ref_status,omitempty"`
}

// Report is the full result of one differential script.
type Report struct {
	Source   string  `json:"source"`
	Name     string  `json:"name"`
	Runs     int     `json:"runs"`
	Passed   bool    `json:"passed"`
	Events   []Event `json:"events"`
	FirstErr string  `json:"first_error,omitempty"`
}

// Run executes the same actions on both engines and returns a report.
// Comparison is strict on (state, value, tag, error kind/code, exception).
func Run(name, src string, actions []Action, cfg engine.Config) (*Report, error) {
	b, err := engine.Frontend(src)
	if err != nil {
		return nil, err
	}
	fsmR, err := engine.New(b, engine.EngineFSM, cfg)
	if err != nil {
		return nil, err
	}
	refR, err := engine.New(b, engine.EngineRef, cfg)
	if err != nil {
		return nil, err
	}
	rep := &Report{Source: src, Name: name, Passed: true}
	fsmGens := map[int]value.Gen{}
	refGens := map[int]value.Gen{}

	for run, a := range actions {
		ev := Event{Run: run, Action: string(a.Kind), Target: a.Target, Comment: a.Comment}
		switch a.Kind {
		case ActNew:
			g1, e1 := fsmR.NewGen(a.GenName, a.Args)
			g2, e2 := refR.NewGen(a.GenName, a.Args)
			ev.FSM = newObs(g1, e1)
			ev.Ref = newObs(g2, e2)
			if compareErr(e1, e2, &ev) {
				fsmGens[a.Target] = g1
				refGens[a.Target] = g2
			}
		case ActNext:
			ev.FSM = observeResult(fsmGens[a.Target].Next())
			ev.Ref = observeResult(refGens[a.Target].Next())
		case ActSend:
			ev.FSM = observeResult(fsmGens[a.Target].Send(a.SendVal))
			ev.Ref = observeResult(refGens[a.Target].Send(a.SendVal))
		case ActSendGen:
			ev.FSM = observeResult(fsmGens[a.Target].Send(value.GenV(fsmGens[a.SendHandle])))
			ev.Ref = observeResult(refGens[a.Target].Send(value.GenV(refGens[a.SendHandle])))
		case ActThrow:
			ex := value.ExV(a.ExName, a.ExMsg)
			ev.FSM = observeResult(fsmGens[a.Target].Throw(ex))
			ev.Ref = observeResult(refGens[a.Target].Throw(ex))
		case ActClose:
			ev.FSM = observeResult(fsmGens[a.Target].Close())
			ev.Ref = observeResult(refGens[a.Target].Close())
		case ActStatus:
			ev.FSM = Observation{State: "status", Status: string(fsmGens[a.Target].Status())}
			ev.Ref = Observation{State: "status", Status: string(refGens[a.Target].Status())}
		}
		if a.Kind != ActNew {
			ev.FSMStatus = string(fsmGens[a.Target].Status())
			ev.RefStatus = string(refGens[a.Target].Status())
		}
		ev.Match = ev.FSM == ev.Ref
		if !ev.Match {
			ev.Reason = explain(ev.FSM, ev.Ref)
			rep.Passed = false
			if rep.FirstErr == "" {
				rep.FirstErr = fmt.Sprintf("run %d action %s: %s", run, a.Kind, ev.Reason)
			}
		}
		rep.Events = append(rep.Events, ev)
		rep.Runs++
	}
	return rep, nil
}

func newObs(g value.Gen, err error) Observation {
	if err != nil {
		return Observation{State: "error", ErrKind: kindOf(err), ErrCode: codeOf(err)}
	}
	return Observation{State: "newborn"}
}

func compareErr(e1, e2 error, ev *Event) bool {
	o1 := Observation{State: "newborn"}
	o2 := Observation{State: "newborn"}
	if e1 != nil {
		o1 = Observation{State: "error", ErrKind: kindOf(e1), ErrCode: codeOf(e1)}
	}
	if e2 != nil {
		o2 = Observation{State: "error", ErrKind: kindOf(e2), ErrCode: codeOf(e2)}
	}
	ev.FSM = o1
	ev.Ref = o2
	return o1 == o2
}

func explain(a, b Observation) string {
	var diffs []string
	if a.State != b.State {
		diffs = append(diffs, fmt.Sprintf("state fsm=%s ref=%s", a.State, b.State))
	}
	if a.Val != b.Val || a.ValTag != b.ValTag {
		diffs = append(diffs, fmt.Sprintf("value fsm=%s(%s) ref=%s(%s)", a.Val, a.ValTag, b.Val, b.ValTag))
	}
	if a.ErrKind != b.ErrKind || a.ErrCode != b.ErrCode {
		diffs = append(diffs, fmt.Sprintf("error fsm=%s/%s ref=%s/%s", a.ErrKind, a.ErrCode, b.ErrKind, b.ErrCode))
	}
	if a.ExName != b.ExName || a.ExMessage != b.ExMessage {
		diffs = append(diffs, fmt.Sprintf("exception fsm=%s:%q ref=%s:%q", a.ExName, a.ExMessage, b.ExName, b.ExMessage))
	}
	if a.Status != b.Status {
		diffs = append(diffs, fmt.Sprintf("status fsm=%s ref=%s", a.Status, b.Status))
	}
	return strings.Join(diffs, "; ")
}
