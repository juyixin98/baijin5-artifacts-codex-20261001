package oracle

import (
	"fmt"
	"sort"
)

// Event is the oracle's observable trace element, using the same vocabulary
// as runtime.Event.
type Event struct {
	Kind     string `json:"kind"`
	Point    string `json:"point,omitempty"`
	Name     string `json:"name,omitempty"`
	Text     string `json:"text,omitempty"`
	Instance int    `json:"instance,omitempty"`
}

// Failure is one observed error. Primary is the first error of the run;
// suppressed errors are those raised later during cleanup.
type Failure struct {
	Category string `json:"category"`
	Code     string `json:"code"`
	Point    string `json:"point,omitempty"`
	Instance int    `json:"instance,omitempty"`
}

// Expected is the independent oracle's full prediction.
type Expected struct {
	ID         string    `json:"id"`
	Status     string    `json:"status"` // ok | return | fail
	ReturnValue string   `json:"return_value,omitempty"`
	Primary    *Failure  `json:"primary,omitempty"`
	Suppressed []Failure `json:"suppressed,omitempty"`
	Events     []Event   `json:"events"`
}

type simRes struct {
	name  string
	point string
	inst  int
	clean []*Node
}

type sim struct {
	c       *Case
	events  []Event
	seq     int
	primary *Failure
	supp    []Failure
	loopSeq int
}

const (
	catInput  = "InputError"
	catState  = "StateConflict"
	catPool   = "ResourceExhausted"
	catComp   = "ComputationFailure"
)

type sig int

const (
	sigNone sig = iota
	sigBreak
	sigReturn
	sigFail
)

type flow struct{ kind sig; value string }

// Simulate runs the independent interpreter over a validated case.
func Simulate(c *Case) *Expected {
	s := &sim{c: c}
	f := s.runSeq(c.Program, nil)
	status := "ok"
	switch f.kind {
	case sigReturn:
		status = "return"
	case sigFail:
		status = "fail"
	case sigBreak:
		status = "fail" // rejected structurally in real programs
	}
	exp := &Expected{ID: c.ID, Status: status, ReturnValue: f.value,
		Primary: s.primary, Suppressed: s.supp, Events: s.events}
	return exp
}

func (s *sim) emit(ev Event) { s.events = append(s.events, ev) }

// runSeq executes a statement sequence with an enclosing instance stack.
// The frame for this sequence is created by the caller; returns terminal flow.
func (s *sim) runSeq(ns []*Node, instStack []int) flow {
	var live []simRes
	for _, n := range ns {
		f, stop := s.runOne(n, &live, instStack)
		if stop {
			s.cleanup(&live, f)
			return f
		}
	}
	s.cleanup(&live, flow{})
	return flow{}
}

func (s *sim) runOne(n *Node, live *[]simRes, instStack []int) (flow, bool) {
	switch n.T {
	case "emit":
		s.emit(Event{Kind: "emit", Text: n.Text})
		return flow{}, false
	case "acquire":
		inst := topInst(instStack)
		point := fmt.Sprintf("init@%d", n.Ord)
		s.emit(Event{Kind: "acquire_start", Point: point, Name: n.Name, Instance: inst})
		if inj, ok := s.findInjection(point, inst); ok {
			s.observe(injError(inj, point, inst), reasonOf(flow{}))
			return flow{kind: sigFail}, true
		}
		s.emit(Event{Kind: "acquire_success", Point: point, Name: n.Name, Instance: inst})
		*live = append(*live, simRes{
			name: n.Name, point: point, inst: inst, clean: n.Cleanup,
		})
		return flow{}, false
	case "fail":
		inst := topInst(instStack)
		point := fmt.Sprintf("fail@%d", n.Ord)
		inj, ok := s.findInjection(point, inst)
		var f Failure
		if ok {
			f = injError(inj, point, inst)
		} else {
			f = Failure{Category: catComp, Code: "EXPLICIT_FAIL", Point: point, Instance: inst}
		}
		s.observe(f, reasonNormal)
		return flow{kind: sigFail}, true
	case "return":
		return flow{kind: sigReturn, value: n.Value}, true
	case "break":
		return flow{kind: sigBreak}, true
	case "scope":
		f := s.runSeq(n.Body, instStack)
		if f.kind == sigNone {
			return flow{}, false
		}
		return f, true
	case "repeat":
		return s.runRepeat(n, instStack), true
	}
	return flow{}, false
}

func (s *sim) runRepeat(n *Node, instStack []int) flow {
	for iter := 1; iter <= n.Count; iter++ {
		stk := append(append([]int{}, instStack...), iter)
		var live []simRes
		f := flow{}
		stopped := false
		for _, b := range n.Body {
			inner, stop := s.runOne(b, &live, stk)
			if stop {
				f = inner
				stopped = true
				break
			}
		}
		_ = stopped
		f = s.cleanup(&live, f)
		switch f.kind {
		case sigBreak:
			return flow{}
		case sigReturn, sigFail:
			return f
		}
	}
	return flow{}
}

// cleanup releases in reverse order under the fixed policy and returns the
// possibly-promoted flow.
func (s *sim) cleanup(live *[]simRes, f flow) flow {
	reason := reasonOf(f)
	for i := len(*live) - 1; i >= 0; i-- {
		r := (*live)[i]
		closePoint := fmt.Sprintf("close@%d", ordOf(r.point))
		s.emit(Event{Kind: "release_start", Point: r.point, Name: r.name, Instance: r.inst})
		s.emit(Event{Kind: "release_done", Point: r.point, Name: r.name, Instance: r.inst})
		for _, cn := range r.clean {
			switch cn.T {
			case "emit":
				s.emit(Event{Kind: "emit", Text: cn.Text})
			case "fail":
				inj, ok := s.findInjection(closePoint, r.inst)
				var fail Failure
				if ok {
					fail = injError(inj, closePoint, r.inst)
				} else {
					fail = Failure{Category: catComp, Code: "CLEANUP_FAILED",
						Point: closePoint, Instance: r.inst}
				}
				reason = s.observe(fail, reason)
				goto promoted
			}
		}
	}
promoted:
	return applyReason(f, reason)
}

const (
	reasonNormal = "normal"
	reasonBreak  = "break"
	reasonReturn = "return"
	reasonFail   = "fail"
)

func reasonOf(f flow) string {
	switch f.kind {
	case sigBreak:
		return reasonBreak
	case sigReturn:
		return reasonReturn
	case sigFail:
		return reasonFail
	default:
		return reasonNormal
	}
}

func applyReason(f flow, reason string) flow {
	switch reason {
	case reasonFail:
		return flow{kind: sigFail}
	case reasonReturn:
		return flow{kind: sigReturn, value: f.value}
	case reasonBreak:
		return flow{kind: sigBreak}
	default:
		return flow{}
	}
}

// observe applies first-error-wins plus normal/break -> fail promotion.
func (s *sim) observe(f Failure, reason string) string {
	if s.primary == nil {
		lf := f
		s.primary = &lf
	} else {
		s.supp = append(s.supp, f)
	}
	if reason == reasonNormal || reason == reasonBreak {
		return reasonFail
	}
	return reason
}

func (s *sim) findInjection(point string, inst int) (Injection, bool) {
	var generic *Injection
	injs := append([]Injection(nil), s.c.Injections...)
	sort.SliceStable(injs, func(i, j int) bool { return injs[i].Point < injs[j].Point })
	for i := range injs {
		inj := injs[i]
		if inj.Point != point {
			continue
		}
		if inst > 0 && inj.Instance == inst {
			return inj, true
		}
		if inj.Instance == 0 {
			in := inj
			generic = &in
		}
	}
	if generic != nil {
		return *generic, true
	}
	return Injection{}, false
}

type []Injection = injectionList

type injectionList []Injection

func injError(inj Injection, point string, inst int) Failure {
	code := inj.Code
	if code == "" {
		switch inj.Category {
		case catInput:
			code = "INJECTED_INPUT"
		case catState:
			code = "INJECTED_STATE_CONFLICT"
		case catPool:
			code = "INJECTED_POOL_EXHAUSTED"
		default:
			code = "INJECTED_COMPUTATION"
		}
	}
	return Failure{Category: inj.Category, Code: code, Point: point, Instance: inst}
}

func topInst(stack []int) int {
	if len(stack) == 0 {
		return 0
	}
	return stack[len(stack)-1]
}

func ordOf(point string) int {
	n := 0
	for i := 0; i < len(point); i++ {
		if point[i] >= '0' && point[i] <= '9' {
			n = n*10 + int(point[i]-'0')
		}
	}
	return n
}
