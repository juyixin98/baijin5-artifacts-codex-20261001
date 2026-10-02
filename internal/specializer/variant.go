package specializer

import (
	"fmt"
	"strconv"
	"strings"

	"funcspec/internal/ir"
)

// patternKey encodes the static/dynamic shape plus the static constants. Two
// calls with the same shape and the same constants reuse one variant (memoized
// recursive specialization); different constants yield separate variants until
// the per-function cap is reached.
func patternKey(fn *ir.Func, static []bool, sval []ir.Value) string {
	var b strings.Builder
	for i := range fn.Params {
		if static[i] {
			b.WriteByte('S')
			if sval[i].Kind == 'b' {
				if sval[i].B {
					b.WriteString("1")
				} else {
					b.WriteString("0")
				}
			} else {
				b.WriteString(strconv.FormatInt(sval[i].I, 10))
			}
		} else {
			b.WriteByte('D')
		}
		b.WriteByte('_')
	}
	return b.String()
}

func generalizedID(fnName string) string { return "$" + fnName + "__gen" }

func specificID(fnName, key string) string {
	return "$" + fnName + "__" + sanitize(key)
}

const genKey = "*"

func sanitize(key string) string {
	r := strings.NewReplacer("-", "m", "/", "d")
	return r.Replace(strings.TrimRight(key, "_"))
}

// requestVariant implements the memoizing, bounded recursive specialization:
//
//  1. exact (pattern+constants) cache hit -> reuse it;
//  2. per-function cap not reached -> create the specific variant;
//  3. cap reached -> create/reuse the single generalized all-dynamic variant.
func (st *state) requestVariant(fnName string, static []bool, sval []ir.Value) (*variant, error) {
	fn := st.src.Funcs[fnName]
	if fn == nil {
		return nil, fmt.Errorf("internal: unknown function %q", fnName)
	}
	set := st.variants[fnName]
	if set == nil {
		set = map[string]*variant{}
		st.variants[fnName] = set
	}

	if !allStaticGeneralized(static) {
		key := patternKey(fn, static, sval)
		if v, ok := set[key]; ok {
			st.stat.CacheHits++
			st.log.Event("variant.hit", "same function/pattern/constants already specialized", map[string]any{
				"func": fnName, "pattern": key, "variant": v.id,
			})
			return v, nil
		}
		if len(set) >= st.cfg.MaxVariantsPerFunc {
			st.stat.Generalized++
			st.log.Event("variant.generalize", "per-function variant cap reached; collapsing to dynamic", map[string]any{
				"func": fnName, "cap": st.cfg.MaxVariantsPerFunc, "pattern": key,
			})
			return st.requestGeneralized(fnName)
		}
		return st.buildSpecific(fnName, key, static, sval, set)
	}
	return st.requestGeneralized(fnName)
}

func allStaticGeneralized(static []bool) bool {
	for _, b := range static {
		if b {
			return false
		}
	}
	return len(static) > 0
}

func (st *state) buildSpecific(fnName, key string, static []bool, sval []ir.Value, set map[string]*variant) (*variant, error) {
	fn := st.src.Funcs[fnName]
	id := specificID(fnName, key)
	v := &variant{id: id, fnName: fnName, static: append([]bool(nil), static...), sval: append([]ir.Value(nil), sval...)}
	// Determine surviving (dynamic) parameter names.
	for i, p := range fn.Params {
		if !static[i] {
			v.params = append(v.params, p)
		}
	}
	// Register placeholder before reducing the body so self-recursion resolves
	// to this same variant instead of looping.
	set[key] = v
	st.byID[id] = v
	st.order = append(st.order, id)
	st.stat.VariantsCreated++

	r := &reducer{st: st, fn: fn, vr: v}
	rootEnv := map[string]ir.Value{}
	for i, p := range fn.Params {
		if static[i] {
			rootEnv[p] = sval[i]
		}
	}
	body, err := r.reduceBlock(fn.Body, rootEnv)
	if err != nil {
		return nil, err
	}
	v.body = body
	st.charge(countStmts(body))
	st.log.Event("variant.create", "new specific variant within per-function cap", map[string]any{
		"func": fnName, "variant": id, "pattern": key, "nodes": countStmts(body),
	})
	return v, nil
}

// requestGeneralized returns the unique all-dynamic variant for fnName,
// building it (and any transitive generalized callees) on demand.
func (st *state) requestGeneralized(fnName string) (*variant, error) {
	set := st.variants[fnName]
	if set == nil {
		set = map[string]*variant{}
		st.variants[fnName] = set
	}
	if v, ok := set[genKey]; ok {
		st.stat.CacheHits++
		return v, nil
	}
	fn := st.src.Funcs[fnName]
	if fn == nil {
		return nil, fmt.Errorf("internal: cannot generalize unknown function %q", fnName)
	}
	id := generalizedID(fnName)
	v := &variant{id: id, fnName: fnName, gen: true, params: append([]string(nil), fn.Params...)}
	set[genKey] = v
	st.byID[id] = v
	st.order = append(st.order, id)
	st.stat.VariantsCreated++
	r := &reducer{st: st, fn: fn, vr: v}
	body, err := r.reduceBlock(fn.Body, map[string]ir.Value{})
	if err != nil {
		return nil, err
	}
	v.body = body
	st.charge(countStmts(body))
	st.log.Event("variant.generalized", "emitted all-dynamic generalized variant (calls closed at assembly)", map[string]any{
		"func": fnName, "variant": id, "nodes": countStmts(body),
	})
	return v, nil
}

func (st *state) charge(n int) {
	st.nodes += n
	if st.nodes > st.cfg.MaxResidualNodes {
		panicAbort(st.nodes)
	}
}

func panicAbort(nodes int) { panic(&budgetAbort{nodes: nodes}) }

// countStmts counts IR nodes contributed by a statement list.
func countStmts(stmts []ir.Stmt) int {
	n := 0
	var ce func(e ir.Expr)
	var cs func(ss []ir.Stmt)
	cs = func(ss []ir.Stmt) {
		for _, s := range ss {
			n++
			switch x := s.(type) {
			case *ir.Let:
				ce(x.Init)
			case *ir.Return:
				ce(x.Value)
			case *ir.ExprStmt:
				ce(x.X)
			case *ir.If:
				ce(x.Cond)
				cs(x.Then)
				cs(x.Else)
			}
		}
	}
	ce = func(e ir.Expr) {
		n++
		switch x := e.(type) {
		case *ir.Unary:
			ce(x.X)
		case *ir.Binary:
			ce(x.X)
			ce(x.Y)
		case *ir.Call:
			for _, a := range x.Args {
				ce(a)
			}
		}
	}
	cs(stmts)
	return n
}

// closeGeneralized rewrites every user call in a generalized body to the
// generalized variant of its callee. All needed generalized variants are
// requested first as placeholders (which terminates recursion even for
// self-calls), then their bodies are reduced one by one, and finally this
// closure pass runs over the whole assembled program.
func (st *state) closeGeneralized(stmts []ir.Stmt) []ir.Stmt {
	var walkE func(e ir.Expr) ir.Expr
	var walkS func(ss []ir.Stmt) []ir.Stmt
	walkS = func(ss []ir.Stmt) []ir.Stmt {
		out := make([]ir.Stmt, len(ss))
		for i, s := range ss {
			switch x := s.(type) {
			case *ir.Let:
				out[i] = &ir.Let{Name: x.Name, Init: walkE(x.Init)}
			case *ir.Return:
				out[i] = &ir.Return{Value: walkE(x.Value)}
			case *ir.ExprStmt:
				out[i] = &ir.ExprStmt{X: walkE(x.X)}
			case *ir.If:
				out[i] = &ir.If{Cond: walkE(x.Cond), Then: walkS(x.Then), Else: walkS(x.Else), Pos: x.Pos}
			default:
				out[i] = s
			}
		}
		return out
	}
	walkE = func(e ir.Expr) ir.Expr {
		switch x := e.(type) {
		case *ir.Unary:
			return &ir.Unary{Op: x.Op, X: walkE(x.X)}
		case *ir.Binary:
			return &ir.Binary{Op: x.Op, X: walkE(x.X), Y: walkE(x.Y)}
		case *ir.Call:
			args := make([]ir.Expr, len(x.Args))
			for i, a := range x.Args {
				args[i] = walkE(a)
			}
			if x.Callee != ir.EmitName {
				callee := x.Callee
				if fn, key, isVariant := parseResidualID(callee); isVariant {
					if key == genKey {
						// already points at a generalized variant: keep it
					} else {
						callee = generalizedID(fn)
					}
				} else if !isGeneralizedVariantID(callee) {
					callee = generalizedID(callee)
				}
				return &ir.Call{Callee: callee, Args: args, Pos: x.Pos}
			}
			return &ir.Call{Callee: x.Callee, Args: args, Pos: x.Pos}
		}
		return e
	}
	return walkS(stmts)
}

// isGeneralizedVariantID reports whether id is already a residual generalized
// variant id ("$name__gen"), as opposed to an original source function name.
func isGeneralizedVariantID(id string) bool {
	const suf = "__gen"
	return len(id) > 1+len(suf) && id[0] == '$' && id[len(id)-len(suf):] == suf
}

// parseResidualID splits a residual variant id "$fn__key" into the original
// function name and the within-function key.
func parseResidualID(id string) (fn, key string, ok bool) {
	if len(id) < 2 || id[0] != '$' {
		return "", "", false
	}
	body := id[1:]
	if isGeneralizedVariantID(id) {
		return body[:len(body)-len("__gen")], genKey, true
	}
	idx := strings.LastIndex(body, "__")
	if idx <= 0 {
		return "", "", false
	}
	return body[:idx], body[idx+2:], true
}

// ensureGeneralizedClosure makes sure every generalized variant referenced by
// another generalized variant exists. Reduction of a generalized body keeps
// original callee names; a callee may only have been specialized specifically
// so far, in which case its generalized variant must still be emitted for the
// residual program to be closed. Placeholder registration makes this a
// terminating fixpoint even over mutual/self recursion.
func (st *state) ensureGeneralizedClosure() {
	for progress := true; progress; {
		progress = false
		snapshot := append([]string(nil), st.order...)
		for _, id := range snapshot {
			vr := st.byID[id]
			if vr == nil {
				continue
			}
			for _, fnName := range collectUserCallees(vr.body) {
				srcName := fnName
				if fn, _, isVariant := parseResidualID(fnName); isVariant {
					srcName = fn
				}
				if st.src.Funcs[srcName] == nil {
					continue
				}
				set2 := st.variants[srcName]
				if set2 != nil {
					if _, exists := set2[genKey]; exists {
						continue
					}
				}
				gv, _ := st.requestGeneralized(srcName)
				if gv != nil {
					progress = true
				}
			}
		}
	}
}

func collectUserCallees(stmts []ir.Stmt) []string {
	seen := map[string]bool{}
	var out []string
	var walkE func(e ir.Expr)
	var walkS func(ss []ir.Stmt)
	walkS = func(ss []ir.Stmt) {
		for _, s := range ss {
			switch x := s.(type) {
			case *ir.Let:
				walkE(x.Init)
			case *ir.Return:
				walkE(x.Value)
			case *ir.ExprStmt:
				walkE(x.X)
			case *ir.If:
				walkE(x.Cond)
				walkS(x.Then)
				walkS(x.Else)
			}
		}
	}
	walkE = func(e ir.Expr) {
		switch x := e.(type) {
		case *ir.Unary:
			walkE(x.X)
		case *ir.Binary:
			walkE(x.X)
			walkE(x.Y)
		case *ir.Call:
			if x.Callee != ir.EmitName && !seen[x.Callee] {
				seen[x.Callee] = true
				out = append(out, x.Callee)
			}
			for _, a := range x.Args {
				walkE(a)
			}
		}
	}
	walkS(stmts)
	return out
}
