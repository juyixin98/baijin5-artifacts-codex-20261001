package semdiff

import (
	"fmt"
	"sort"
	"strings"

	"mylnk/internal/ir"
	"mylnk/internal/linker"
	"mylnk/internal/logx"
	"mylnk/internal/objfmt"
	"mylnk/internal/runtime"
)

// ObjectLoader loads a named fixture object.
type ObjectLoader func(name string) (*objfmt.Object, error)

// Mismatch is one concrete expectation failure.
type Mismatch struct {
	Case   string
	Kind   string // return-value, error-category, kept-set, dropped-set, ...
	Reason string
}

func (m *Mismatch) Error() string {
	return fmt.Sprintf("[case %s] %s: %s", m.Case, m.Kind, m.Reason)
}

// Report is the full differential report for one case.
type Report struct {
	Case     string
	Passed   bool
	Steps    []string
	Mismatch *Mismatch
}

func nameOf(gs *ir.Section) string { return gs.Obj.Name + ":" + gs.Name }

// RunCase executes one expectation and returns a verdict report with steps.
func RunCase(e *Expectation, load ObjectLoader, log *logx.Logger) *Report {
	rep := &Report{Case: e.ID}
	addStep := func(s string) {
		rep.Steps = append(rep.Steps, s)
		log.Step("%s: %s", e.ID, s)
	}

	addStep(fmt.Sprintf("loading %d fixture object(s): %s", len(e.Objects), strings.Join(e.Objects, ", ")))
	objs := make([]*objfmt.Object, 0, len(e.Objects))
	for _, name := range e.Objects {
		o, err := load(name)
		if err != nil {
			rep.Mismatch = &Mismatch{Case: e.ID, Kind: "fixture-load", Reason: err.Error()}
			return rep
		}
		objs = append(objs, o)
	}

	addStep("building IR and resolving strong/weak symbols")
	p, err := ir.Build(objs, e.Entry)
	if err != nil {
		rep.evaluateLinkError(e, err, addStep)
		verdictIfPassed(rep, log)
		return rep
	}

	addStep("computing GC roots and reachability closure")
	rr := p.AnalyzeReach()

	addStep("checking undefined symbols in reachable sections")
	undefErrs := p.UndefinedDiagnostics(rr)
	if mm := compareUndefined(e, undefErrs); mm != nil {
		return finishMismatch(rep, mm, log)
	}
	if len(e.WantUndefined) > 0 {
		addStep(fmt.Sprintf("undefined-symbol diagnostics matched expectation: %v", e.WantUndefined))
		// A case that only expects undefined errors is complete here.
		if e.WantErrorKind == ir.KindUndefinedSymbol {
			rep.Passed = true
			log.Verdict(true, "%s: expected undefined diagnostics observed", e.ID)
			return rep
		}
	}

	img, err := linker.Link(p, rr)
	if err != nil {
		rep.evaluateLinkError(e, err, addStep)
		verdictIfPassed(rep, log)
		return rep
	}

	kept := map[string]bool{}
	dropped := map[string]bool{}
	for _, gs := range p.Order {
		if rr.Kept[gs.ID] {
			kept[nameOf(gs)] = true
		} else {
			dropped[nameOf(gs)] = true
		}
	}
	addStep(fmt.Sprintf("section verdict: kept=%v dropped=%v", keys(kept), keys(dropped)))
	if mm := setMismatch(e.ID, "kept-set", e.WantKept, kept); mm != nil {
		return finishMismatch(rep, mm, log)
	}
	if mm := setMismatch(e.ID, "dropped-set", e.WantDropped, dropped); mm != nil {
		return finishMismatch(rep, mm, log)
	}

	if e.WantErrorKind == "runtime" || e.WantReturnSet {
		addStep(fmt.Sprintf("running image entry=%s pc=%#x", e.Entry, img.EntryPC))
		res, rerr := runtime.Run(img, 0, false)
		if rerr != nil {
			le, ok := rerr.(*ir.LinkError)
			kind := "<non-categorised>"
			if ok {
				kind = le.Kind
			}
			if e.WantErrorKind == "runtime" {
				if kind == ir.KindRuntime {
					addStep(fmt.Sprintf("vm failed as expected (%s): %v", kind, rerr))
					rep.Passed = true
					log.Verdict(true, "%s: runtime error expected and observed: %v", e.ID, rerr)
					return rep
				}
				rep.Mismatch = &Mismatch{Case: e.ID, Kind: "error-category",
					Reason: fmt.Sprintf("runtime failure kind=%s want runtime", kind)}
				return finishMismatch(rep, rep.Mismatch, log)
			}
			rep.Mismatch = &Mismatch{Case: e.ID, Kind: "runtime", Reason: rerr.Error()}
			return finishMismatch(rep, rep.Mismatch, log)
		}
		if e.WantErrorKind == "runtime" {
			rep.Mismatch = &Mismatch{Case: e.ID, Kind: "error-category",
				Reason: "expected runtime error but VM halted successfully"}
			return finishMismatch(rep, rep.Mismatch, log)
		}
		addStep(fmt.Sprintf("vm halted after %d steps with return=%d", res.Steps, res.Return))
		if res.Return != e.WantReturn {
			rep.Mismatch = &Mismatch{Case: e.ID, Kind: "return-value",
				Reason: fmt.Sprintf("got %d want %d", res.Return, e.WantReturn)}
			return finishMismatch(rep, rep.Mismatch, log)
		}
	}

	if e.WantErrorKind != "" {
		rep.Mismatch = &Mismatch{Case: e.ID, Kind: "error-category",
			Reason: fmt.Sprintf("expected error %s but link/run succeeded", e.WantErrorKind)}
		return finishMismatch(rep, rep.Mismatch, log)
	}
	rep.Passed = true
	log.Verdict(true, "%s: expectation satisfied", e.ID)
	return rep
}

func finishMismatch(r *Report, mm *Mismatch, log *logx.Logger) *Report {
	r.Mismatch = mm
	log.Verdict(false, "%s: %s", mm.Case, mm.Error())
	return r
}

func (r *Report) evaluateLinkError(e *Expectation, err error, add func(string)) *Report {
	le, ok := err.(*ir.LinkError)
	kind := "<non-categorised>"
	if ok {
		kind = le.Kind
	}
	add(fmt.Sprintf("link reported error kind=%s: %v", kind, err))
	if e.WantErrorKind == "" {
		r.Mismatch = &Mismatch{Case: e.ID, Kind: "unexpected-error", Reason: err.Error()}
		return r
	}
	if kind != e.WantErrorKind {
		r.Mismatch = &Mismatch{Case: e.ID, Kind: "error-category",
			Reason: fmt.Sprintf("got %s want %s", kind, e.WantErrorKind)}
		return r
	}
	r.Passed = true
	return r
}

func verdictIfPassed(r *Report, log *logx.Logger) {
	if r.Passed {
		log.Verdict(true, "%s: expected error category observed", r.Case)
	} else if r.Mismatch != nil {
		log.Verdict(false, "%s: %s", r.Case, r.Mismatch.Error())
	}
}

func compareUndefined(e *Expectation, errs []*ir.LinkError) *Mismatch {
	got := map[string]bool{}
	want := map[string]bool{}
	for _, n := range e.WantUndefined {
		want[n] = true
	}
	for _, le := range errs {
		msg := le.Msg
		if i := strings.Index(msg, "undefined symbol "); i >= 0 {
			rest := msg[i+len("undefined symbol "):]
			got[strings.SplitN(rest, " ", 2)[0]] = true
		}
	}
	if len(want) == 0 && len(got) == 0 {
		return nil
	}
	if len(want) != len(got) {
		return &Mismatch{Case: e.ID, Kind: "undefined-set",
			Reason: fmt.Sprintf("got %v want %v", keys(got), keys(want))}
	}
	for n := range want {
		if !got[n] {
			return &Mismatch{Case: e.ID, Kind: "undefined-set", Reason: "missing diagnostic for " + n}
		}
	}
	return nil
}

func setMismatch(id, kind string, want []string, got map[string]bool) *Mismatch {
	wantSet := map[string]bool{}
	for _, w := range want {
		wantSet[w] = true
	}
	// Empty expectation means "do not assert this set".
	if len(wantSet) == 0 {
		return nil
	}
	if len(wantSet) != len(got) {
		return &Mismatch{Case: id, Kind: kind,
			Reason: fmt.Sprintf("got %v want %v", keys(got), keys(wantSet))}
	}
	for w := range wantSet {
		if !got[w] {
			return &Mismatch{Case: id, Kind: kind, Reason: "missing " + w}
		}
	}
	return nil
}

func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
