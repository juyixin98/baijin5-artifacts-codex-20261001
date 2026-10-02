// Package specializer performs restricted partial evaluation of first-order IR
// programs.
//
// Restrictions (enforced, not assumed):
//
//   - Only expressions whose every dependency is a compile-time known value and
//     that belong to a pure computation are evaluated ahead of time. Calls to
//     the impure builtin "emit" and calls to impure functions are never folded;
//     they are always residualized so side effects keep their run-time order.
//   - Recursive specialization is memoized per (function, static-pattern) and
//     bounded per function; once the bound is reached, further patterns are
//     generalized to one all-dynamic variant, so residual code cannot grow
//     without limit even for unbounded chains of static arguments.
//   - A global residual-node budget can abort specialization. In the default
//     mode this falls back to an identity residual program (semantics
//     preserved); in strict-budget mode it is reported as E_BUDGET.
package specializer

import (
	"fmt"

	"funcspec/internal/config"
	"funcspec/internal/errcat"
	"funcspec/internal/ir"
	"funcspec/internal/observ"
)

// Arg is one top-level entry argument: either a known static constant or an
// unknown dynamic input.
type Arg struct {
	Static bool
	Value  ir.Value
}

// Stat summarizes what specialization did, for tests and logs.
type Stat struct {
	EntryVariant    string `json:"entry_variant"`
	VariantsCreated int    `json:"variants_created"`
	ResidualNodes   int    `json:"residual_nodes"`
	CallsFolded     int    `json:"calls_folded"`
	CacheHits       int    `json:"cache_hits"`
	Generalized     int    `json:"generalized"`
	BranchesPruned  int    `json:"branches_pruned"`
	Fallback        string `json:"fallback,omitempty"` // "identity" when global budget fired
	FallbackReason  string `json:"fallback_reason,omitempty"`
}

// Result is the output of specializing one entry call.
type Result struct {
	// Entry is the function to invoke in the residual program.
	Entry string
	// Residual is the specialized program (or an identity copy on fallback).
	Residual *ir.Program
	// StaticResult is non-nil when the whole entry call folded to a constant.
	StaticResult *ir.Value
	// Args are the residual entry arguments: only dynamic inputs survive.
	Args []ir.Expr
	Stat Stat
}

// budgetAbort is raised internally when the global node budget is exhausted;
// the entry driver converts it into fallback or an E_BUDGET error.
type budgetAbort struct{ nodes int }

func (b *budgetAbort) Error() string {
	return fmt.Sprintf("residual node budget exhausted at %d", b.nodes)
}

type state struct {
	src      *ir.Program
	cfg      config.Config
	log      *observ.Logger
	variants map[string]map[string]*variant // func -> pattern key -> variant
	byID     map[string]*variant            // residual id -> variant
	order    []string                       // stable residual emission order
	nodes    int
	folds    int
	stat     Stat
}

type variant struct {
	id     string
	fnName string
	gen    bool
	// static[i] valid when the original parameter i is static in this variant.
	static []bool
	sval   []ir.Value
	body   []ir.Stmt
	params []string // residual parameter names, dynamic originals only
}

// Specialize specializes src.Entry(argv), logging every decision to log (which
// may be a discard logger).
func Specialize(src *ir.Program, entry string, argv []Arg, cfg config.Config, log *observ.Logger) (*Result, error) {
	if _, ok := src.Funcs[entry]; !ok {
		return nil, errcat.New(errcat.UndefinedSymbol, "no entry function %q", entry)
	}
	st := &state{src: src, cfg: cfg, log: log, variants: map[string]map[string]*variant{}, byID: map[string]*variant{}}
	res, err := st.runEntry(entry, argv)
	if err != nil {
		if ba, ok := err.(*budgetAbort); ok {
			if cfg.StrictBudget {
				st.log.Event("budget.exceeded", "residual nodes > max_residual_nodes", map[string]any{
					"nodes": ba.nodes, "budget": cfg.MaxResidualNodes,
				})
				return nil, errcat.New(errcat.BudgetExceeded, "residual node budget exhausted at %d > %d", ba.nodes, cfg.MaxResidualNodes)
			}
			id := identity(src, entry, argv)
			id.Stat.Fallback = "identity"
			id.Stat.FallbackReason = fmt.Sprintf("residual nodes exceeded %d (aborted at %d)", cfg.MaxResidualNodes, ba.nodes)
			st.log.Event("budget.fallback", "global node budget exhausted; identity residual preserves semantics", map[string]any{
				"nodes": ba.nodes, "budget": cfg.MaxResidualNodes,
			})
			return id, nil
		}
		return nil, err
	}
	return res, nil
}

func (st *state) specializeEntry(entry string, argv []Arg) (res *Result, err error) {
	fn := st.src.Funcs[entry]
	static := make([]bool, len(argv))
	sval := make([]ir.Value, len(argv))
	for i, a := range argv {
		static[i] = a.Static
		sval[i] = a.Value
	}
	pattern := make([]bool, len(fn.Params))
	copy(pattern, static)

	// Rule 1: a pure call whose arguments are all known may be executed ahead
	// of time, bounded by the fold budget.
	if fn.Pure && allStatic(static) {
		if v, ferr := st.foldPure(entry, staticValues(argv)); ferr == nil {
			st.stat.CallsFolded++
			st.log.Event("entry.fold", "pure entry + all args static + within fold budget", map[string]any{
				"entry": entry, "value": valueDetail(v),
			})
			st.stat.ResidualNodes = st.nodes
			return &Result{Entry: entry, StaticResult: &v, Args: nil, Stat: st.stat, Residual: &ir.Program{Funcs: map[string]*ir.Func{}}}, nil
		} else {
			st.log.Event("entry.fold.skipped", "static pure entry could not be fully folded; residualizing", map[string]any{
				"entry": entry, "reason": ferr.Error(),
			})
		}
	}

	v, err := st.requestVariant(entry, pattern, sval)
	if err != nil {
		return nil, err
	}
	resArgs := make([]ir.Expr, 0)
	for i := range fn.Params {
		if !static[i] {
			resArgs = append(resArgs, &ir.Var{Name: fn.Params[i]})
		}
	}
	st.stat.EntryVariant = v.id
	st.log.Event("entry.variant", "residual entry variant selected", map[string]any{
		"entry": entry, "variant": v.id, "residual_args": len(resArgs),
	})
	return &Result{Entry: v.id, Residual: st.emitProgram(), Args: resArgs, Stat: st.stat}, nil
}

func staticValues(argv []Arg) []ir.Value {
	vs := make([]ir.Value, len(argv))
	for i, a := range argv {
		vs[i] = a.Value
	}
	return vs
}

func allStatic(s []bool) bool {
	for _, b := range s {
		if !b {
			return false
		}
	}
	return true
}

// emitProgram assembles created variants into an IR program in creation order.
func (st *state) emitProgram() *ir.Program {
	st.ensureGeneralizedClosure()
	funcs := map[string]*ir.Func{}
	out := &ir.Program{Funcs: funcs}
	for _, id := range st.order {
		vr := st.byID[id]
		if vr == nil {
			continue
		}
		body := st.resolveCalls(vr.body)
		funcs[id] = &ir.Func{Name: id, Params: vr.params, Pure: false, Body: body}
	}
	out.Order = append(out.Order, st.order...)
	st.stat.ResidualNodes = st.nodes
	return out
}

func valueDetail(v ir.Value) any {
	if v.Kind == 'b' {
		return v.B
	}
	return v.I
}

// runEntry runs specializeEntry while converting a global-budget panic into a
// recoverable *budgetAbort error.
func (st *state) runEntry(entry string, argv []Arg) (res *Result, err error) {
	defer func() {
		if r := recover(); r != nil {
			if ba, ok := r.(*budgetAbort); ok {
				err = ba
				return
			}
			panic(r)
		}
	}()
	return st.specializeEntry(entry, argv)
}
