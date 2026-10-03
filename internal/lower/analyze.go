package lower

import (
	"fmt"
	"sort"
	"strings"

	"rlc/internal/diag"
	"rlc/internal/frontend"
)

type DepKind string

const (
	DepCall        DepKind = "call"
	DepInlineConst DepKind = "inline-const"
	DepGenericBody DepKind = "generic-body"
)

type Dep struct {
	Kind   DepKind
	Target string
}

type SymbolKind string

const (
	SymFunc  SymbolKind = "fn"
	SymConst SymbolKind = "const"
)

type SymbolInfo struct {
	Module   string
	Short    string
	Key      string
	Kind     SymbolKind
	Pub      bool
	Generic  bool
	Params   []frontend.Param
	Result   string
	ConstVal *irValue
	ConstTy  string
}

// SymbolFacts is the analyzer output consumed by fingerprinting and codegen.
type SymbolFacts struct {
	Key      string
	Module   string
	Short    string
	Kind     SymbolKind
	Pub      bool
	Generic  bool
	SigText  string
	BodyText string
	Deps     []Dep
	Info     *SymbolInfo
}

type ModuleAnalysis struct {
	Module string
	AST    *frontend.Module
	Facts  []*SymbolFacts
	Order  []string // global keys in source order
}

type Analysis struct {
	Order    []string // module order (imports first)
	ASTs     map[string]*frontend.Module
	Modules  map[string]*ModuleAnalysis
	Symbols  map[string]*SymbolInfo
	ConstVal map[string]*irValue
}

type irValue = struct {
	ty string
	i  int64
	s  string
	b  bool
}

type analyzer struct {
	mods     map[string]*frontend.Module
	syms     map[string]*SymbolInfo
	constVal map[string]*irValue
	log      *diag.Logger
	order    []string
	result   *Analysis
}

func Analyze(mods map[string]*frontend.Module, log *diag.Logger) (*Analysis, error) {
	order, err := topoOrder(mods)
	if err != nil {
		return nil, err
	}
	a := &analyzer{
		mods:     mods,
		syms:     map[string]*SymbolInfo{},
		constVal: map[string]*irValue{},
		log:      log,
		order:    order,
	}
	if err := a.index(); err != nil {
		return nil, err
	}
	if err := a.foldConsts(); err != nil {
		return nil, err
	}
	out := &Analysis{Order: order, ASTs: a.mods, Modules: map[string]*ModuleAnalysis{}, Symbols: a.syms, ConstVal: a.constVal}
	a.result = out
	for _, m := range order {
		ma, err := a.analyzeModule(m)
		if err != nil {
			return nil, err
		}
		out.Modules[m] = ma
	}
	return out, nil
}

func topoOrder(mods map[string]*frontend.Module) ([]string, error) {
	names := make([]string, 0, len(mods))
	for n := range mods {
		names = append(names, n)
	}
	sort.Strings(names)
	visited := map[string]int{} // 0 unseen, 1 active, 2 done
	var order []string
	var visit func(n string) error
	visit = func(n string) error {
		switch visited[n] {
		case 1:
			return fmt.Errorf("cyclic module import involving %q", n)
		case 2:
			return nil
		}
		visited[n] = 1
		deps := append([]string{}, mods[n].Imports...)
		sort.Strings(deps)
		for _, d := range deps {
			if _, ok := mods[d]; !ok {
				return fmt.Errorf("module %q imports unknown module %q", n, d)
			}
			if err := visit(d); err != nil {
				return err
			}
		}
		visited[n] = 2
		order = append(order, n)
		return nil
	}
	for _, n := range names {
		if err := visit(n); err != nil {
			return nil, err
		}
	}
	return order, nil
}

func keyOf(mod, short string) string { return mod + "." + short }

func (a *analyzer) index() error {
	for _, m := range a.order {
		ast := a.mods[m]
		for _, d := range ast.Decls {
			switch t := d.(type) {
			case *frontend.FnDecl:
				k := keyOf(m, t.Name)
				if _, exists := a.syms[k]; exists {
					return fmt.Errorf("module %s: duplicate symbol %s", m, t.Name)
				}
				if t.Generic && !t.Pub {
					return fmt.Errorf("module %s: generic fn %s must be pub", m, t.Name)
				}
				a.syms[k] = &SymbolInfo{
					Module: m, Short: t.Name, Key: k, Kind: SymFunc, Pub: t.Pub,
					Generic: t.Generic, Params: t.Params, Result: t.Result,
				}
			case *frontend.ConstDecl:
				k := keyOf(m, t.Name)
				if _, exists := a.syms[k]; exists {
					return fmt.Errorf("module %s: duplicate symbol %s", m, t.Name)
				}
				a.syms[k] = &SymbolInfo{
					Module: m, Short: t.Name, Key: k, Kind: SymConst, Pub: t.Pub,
					ConstTy: t.Type,
				}
			}
		}
	}
	return nil
}

func (a *analyzer) foldConsts() error {
	state := map[string]int{}
	var constValue func(k string, seen map[string]bool) (*irValue, error)
	var fold func(k string, e frontend.Expr, seen map[string]bool) (*irValue, error)
	constValue = func(k string, seen map[string]bool) (*irValue, error) {
		if v, ok := a.constVal[k]; ok {
			return v, nil
		}
		if state[k] == 1 {
			return nil, fmt.Errorf("cyclic constant definition at %s", k)
		}
		state[k] = 1
		info := a.syms[k]
		var cd *frontend.ConstDecl
		for _, d := range a.mods[info.Module].Decls {
			if c, ok := d.(*frontend.ConstDecl); ok && c.Name == info.Short {
				cd = c
			}
		}
		ns := map[string]bool{}
		for kk := range seen {
			ns[kk] = true
		}
		ns[k] = true
		v, err := fold(k, cd.Value, ns)
		if err != nil {
			return nil, err
		}
		if v.ty != info.ConstTy {
			return nil, fmt.Errorf("const %s: declared %s but value is %s", k, info.ConstTy, v.ty)
		}
		state[k] = 2
		a.constVal[k] = v
		return v, nil
	}
	fold = func(k string, e frontend.Expr, seen map[string]bool) (*irValue, error) {
		switch t := e.(type) {
		case *frontend.IntLit:
			return &irValue{ty: "int", i: t.Value}, nil
		case *frontend.StrLit:
			return &irValue{ty: "string", s: t.Value}, nil
		case *frontend.BoolLit:
			return &irValue{ty: "bool", b: t.Value}, nil
		case *frontend.IdentExpr:
			rk, info, err := a.result.resolve(k[:strings.Index(k, ".")], t.Name)
			if err != nil {
				return nil, err
			}
			if info.Kind != SymConst {
				return nil, fmt.Errorf("const %s: %s is not a constant", k, t.Name)
			}
			if seen[rk] {
				return nil, fmt.Errorf("cyclic constant reference involving %s", rk)
			}
			return constValue(rk, seen)
		case *frontend.UnaryExpr:
			v, err := fold(k, t.Inner, seen)
			if err != nil {
				return nil, err
			}
			if v.ty != "int" || t.Op != "-" {
				return nil, fmt.Errorf("const %s: unary %s requires int", k, t.Op)
			}
			return &irValue{ty: "int", i: -v.i}, nil
		case *frontend.BinaryExpr:
			l, err := fold(k, t.Lhs, seen)
			if err != nil {
				return nil, err
			}
			r, err := fold(k, t.Rhs, seen)
			if err != nil {
				return nil, err
			}
			if l.ty != "int" || r.ty != "int" {
				return nil, fmt.Errorf("const %s: operator %s requires int operands", k, t.Op)
			}
			v, err := intOp(t.Op, l.i, r.i)
			if err != nil {
				return nil, fmt.Errorf("const %s: %v", k, err)
			}
			return &irValue{ty: "int", i: v}, nil
		default:
			return nil, fmt.Errorf("const %s: unsupported initializer %T", k, e)
		}
	}
	for _, m := range a.order {
		for _, d := range a.mods[m].Decls {
			if c, ok := d.(*frontend.ConstDecl); ok {
				if _, err := constValue(keyOf(m, c.Name), map[string]bool{}); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func intOp(op string, l, r int64) (int64, error) {
	switch op {
	case "+":
		return l + r, nil
	case "-":
		return l - r, nil
	case "*":
		return l * r, nil
	case "/":
		if r == 0 {
			return 0, fmt.Errorf("division by zero")
		}
		return l / r, nil
	case "%":
		if r == 0 {
			return 0, fmt.Errorf("modulo by zero")
		}
		return l % r, nil
	}
	return 0, fmt.Errorf("operator %s invalid on int", op)
}

// resolve maps an optionally qualified reference to a global symbol key.
func (an *Analysis) resolve(curMod, name string) (string, *SymbolInfo, error) {
	if i := strings.Index(name, "::"); i >= 0 {
		mod, short := name[:i], name[i+2:]
		k := keyOf(mod, short)
		info, ok := an.Symbols[k]
		if !ok {
			return "", nil, fmt.Errorf("unresolved %s", name)
		}
		if mod != curMod && !info.Pub {
			return "", nil, fmt.Errorf("%s is not public", k)
		}
		if mod != curMod && !imports(an.ASTs[curMod], mod) {
			return "", nil, fmt.Errorf("module %s does not import %s", curMod, mod)
		}
		return k, info, nil
	}
	k := keyOf(curMod, name)
	if info, ok := an.Symbols[k]; ok {
		return k, info, nil
	}
	var found string
	for _, imp := range an.ASTs[curMod].Imports {
		ik := keyOf(imp, name)
		if info, ok := an.Symbols[ik]; ok && info.Pub {
			if found != "" {
				return "", nil, fmt.Errorf("ambiguous reference %s: %s or %s", name, found, ik)
			}
			found = ik
		}
	}
	if found == "" {
		return "", nil, fmt.Errorf("unresolved %s in module %s", name, curMod)
	}
	return found, an.Symbols[found], nil
}

func imports(m *frontend.Module, target string) bool {
	for _, im := range m.Imports {
		if im == target {
			return true
		}
	}
	return false
}

func (a *analyzer) analyzeModule(m string) (*ModuleAnalysis, error) {
	ast := a.mods[m]
	ma := &ModuleAnalysis{Module: m, AST: ast}
	// Import visibility check for functions/consts happens at resolve time.
	for _, d := range ast.Decls {
		switch t := d.(type) {
		case *frontend.FnDecl:
			k := keyOf(m, t.Name)
			facts, err := a.analyzeFunc(m, k, t, map[string]string{}, true)
			if err != nil {
				return nil, err
			}
			ma.Facts = append(ma.Facts, facts)
			ma.Order = append(ma.Order, k)
		case *frontend.ConstDecl:
			k := keyOf(m, t.Name)
			deps := []Dep{}
			a.collectConstDeps(m, k, t.Value, map[string]bool{}, &deps)
			deps = dedupDeps(deps)
			info := a.syms[k]
			facts := &SymbolFacts{
				Key: k, Module: m, Short: t.Name, Kind: SymConst, Pub: t.Pub,
				SigText:  constSigText(info),
				BodyText: Canon(t),
				Deps:     deps,
				Info:     info,
			}
			ma.Facts = append(ma.Facts, facts)
			ma.Order = append(ma.Order, k)
		}
	}
	if a.log != nil {
		a.log.WithModule(m).Accept("", fmt.Sprintf("analyzed %d symbols", len(ma.Facts)),
			map[string]any{"symbols": len(ma.Facts), "imports": len(ast.Imports)})
	}
	return ma, nil
}

func (a *analyzer) analyzeFunc(m, k string, fn *frontend.FnDecl, subst map[string]string, enforceGeneric bool) (*SymbolFacts, error) {
	env := map[string]string{}
	for _, p := range fn.Params {
		if err := checkType(p.Type, fn.Generic, subst); err != nil {
			return nil, fmt.Errorf("fn %s: %v", k, err)
		}
		env[p.Name] = concrete(p.Type, subst)
	}
	if fn.Result != "" {
		if err := checkType(fn.Result, fn.Generic, subst); err != nil {
			return nil, fmt.Errorf("fn %s: %v", k, err)
		}
	}
	deps := []Dep{}
	for _, s := range fn.Body {
		if err := a.checkStmt(m, k, s, env, fn, subst, &deps); err != nil {
			return nil, err
		}
	}
	if fn.Result != "" {
		rt, err := a.bodyReturnsType(m, k, fn.Body, env, fn, subst)
		if err != nil {
			return nil, err
		}
		if rt != concrete(fn.Result, subst) {
			return nil, fmt.Errorf("fn %s: result %s but returns %s", k, fn.Result, rt)
		}
	}
	deps = dedupDeps(deps)
	return &SymbolFacts{
		Key: k, Module: m, Short: fn.Name, Kind: SymFunc, Pub: fn.Pub, Generic: fn.Generic,
		SigText:  funcSigText(a.syms[k]),
		BodyText: Canon(fn),
		Deps:     deps,
		Info:     a.syms[k],
	}, nil
}

func checkType(ty string, genericAllowed bool, subst map[string]string) error {
	switch ty {
	case "int", "string", "bool":
		return nil
	case "T":
		if !genericAllowed {
			return fmt.Errorf("type variable T used in non-generic function")
		}
		return nil
	}
	return fmt.Errorf("unknown type %q", ty)
}

func concrete(ty string, subst map[string]string) string {
	if ty == "T" {
		if c, ok := subst["T"]; ok {
			return c
		}
	}
	return ty
}

func (a *analyzer) checkStmt(m, k string, s frontend.Stmt, env map[string]string, fn *frontend.FnDecl, subst map[string]string, deps *[]Dep) error {
	switch t := s.(type) {
	case *frontend.LetStmt:
		ty, err := a.checkExpr(m, k, t.Value, env, fn, subst, deps)
		if err != nil {
			return err
		}
		if t.Type != "" {
			if err := checkType(t.Type, fn.Generic, subst); err != nil {
				return err
			}
			if concrete(t.Type, subst) != ty {
				return fmt.Errorf("fn %s: let %s expects %s, got %s", k, t.Name, t.Type, ty)
			}
		}
		env[t.Name] = ty
	case *frontend.ReturnStmt:
		if t.Has {
			if _, err := a.checkExpr(m, k, t.Value, env, fn, subst, deps); err != nil {
				return err
			}
		}
	case *frontend.ExprStmt:
		if _, err := a.checkExpr(m, k, t.Expr, env, fn, subst, deps); err != nil {
			return err
		}
	case *frontend.IfStmt:
		ct, err := a.checkExpr(m, k, t.Cond, env, fn, subst, deps)
		if err != nil {
			return err
		}
		if ct != "bool" {
			return fmt.Errorf("fn %s: if condition must be bool, got %s", k, ct)
		}
		for _, bs := range t.Then {
			if err := a.checkStmt(m, k, bs, env, fn, subst, deps); err != nil {
				return err
			}
		}
		for _, bs := range t.Else {
			if err := a.checkStmt(m, k, bs, env, fn, subst, deps); err != nil {
				return err
			}
		}
	}
	return nil
}

func (a *analyzer) checkExpr(m, k string, e frontend.Expr, env map[string]string, fn *frontend.FnDecl, subst map[string]string, deps *[]Dep) (string, error) {
	switch t := e.(type) {
	case *frontend.IntLit:
		return "int", nil
	case *frontend.StrLit:
		return "string", nil
	case *frontend.BoolLit:
		return "bool", nil
	case *frontend.IdentExpr:
		if ty, ok := env[t.Name]; ok {
			return ty, nil
		}
		rk, info, err := a.result.resolve(m, t.Name)
		if err != nil {
			return "", fmt.Errorf("fn %s: %v", k, err)
		}
		if info.Kind != SymConst {
			return "", fmt.Errorf("fn %s: %s is not a value", k, t.Name)
		}
		if info.Pub {
			*deps = append(*deps, Dep{Kind: DepInlineConst, Target: rk})
		}
		return info.ConstTy, nil
	case *frontend.CallExpr:
		rk, info, err := a.result.resolve(m, t.Name)
		if err != nil {
			return "", fmt.Errorf("fn %s: %v", k, err)
		}
		if info.Kind != SymFunc {
			return "", fmt.Errorf("fn %s: %s is not a function", k, t.Name)
		}
		argTys := make([]string, len(t.Args))
		for i, ar := range t.Args {
			ty, err := a.checkExpr(m, k, ar, env, fn, subst, deps)
			if err != nil {
				return "", err
			}
			argTys[i] = ty
		}
		if len(argTys) != len(info.Params) {
			return "", fmt.Errorf("fn %s: call %s arity %d != %d", k, rk, len(argTys), len(info.Params))
		}
		instSubst := map[string]string{}
		if info.Generic {
			for i, p := range info.Params {
				if p.Type == "T" {
					instSubst["T"] = argTys[i]
				} else if p.Type != argTys[i] {
					return "", fmt.Errorf("fn %s: call %s arg %d expects %s, got %s", k, rk, i, p.Type, argTys[i])
				}
			}
			*deps = append(*deps, Dep{Kind: DepGenericBody, Target: rk})
		} else {
			for i, p := range info.Params {
				if p.Type != argTys[i] {
					return "", fmt.Errorf("fn %s: call %s arg %d expects %s, got %s", k, rk, i, p.Type, argTys[i])
				}
			}
			*deps = append(*deps, Dep{Kind: DepCall, Target: rk})
		}
		res := info.Result
		if res == "T" {
			res = instSubst["T"]
		}
		return res, nil
	case *frontend.UnaryExpr:
		ty, err := a.checkExpr(m, k, t.Inner, env, fn, subst, deps)
		if err != nil {
			return "", err
		}
		if t.Op == "-" && ty == "int" {
			return "int", nil
		}
		return "", fmt.Errorf("fn %s: invalid unary %s on %s", k, t.Op, ty)
	case *frontend.BinaryExpr:
		l, err := a.checkExpr(m, k, t.Lhs, env, fn, subst, deps)
		if err != nil {
			return "", err
		}
		r, err := a.checkExpr(m, k, t.Rhs, env, fn, subst, deps)
		if err != nil {
			return "", err
		}
		switch t.Op {
		case "+", "-", "*", "/", "%":
			if l != "int" || r != "int" {
				return "", fmt.Errorf("fn %s: %s requires int operands (%s,%s)", k, t.Op, l, r)
			}
			return "int", nil
		case "<", "<=", ">", ">=":
			if l != "int" || r != "int" {
				return "", fmt.Errorf("fn %s: %s requires int operands", k, t.Op)
			}
			return "bool", nil
		case "==", "!=":
			if l != r {
				return "", fmt.Errorf("fn %s: %s requires matching operands (%s,%s)", k, t.Op, l, r)
			}
			return "bool", nil
		}
		return "", fmt.Errorf("fn %s: unknown operator %s", k, t.Op)
	}
	return "", fmt.Errorf("fn %s: unknown expression %T", k, e)
}

func (a *analyzer) bodyReturnsType(m, k string, body []frontend.Stmt, env map[string]string, fn *frontend.FnDecl, subst map[string]string) (string, error) {
	for _, s := range body {
		if r, ok := s.(*frontend.ReturnStmt); ok && r.Has {
			return a.checkExpr(m, k, r.Value, env, fn, subst, &[]Dep{})
		}
		if is, ok := s.(*frontend.IfStmt); ok && len(is.Else) > 0 {
			t1, e1 := a.bodyReturnsType(m, k, is.Then, env, fn, subst)
			if e1 == nil {
				t2, e2 := a.bodyReturnsType(m, k, is.Else, env, fn, subst)
				if e2 == nil && t1 == t2 {
					return t1, nil
				}
			}
		}
	}
	return "", fmt.Errorf("fn %s: missing return value", k)
}

func (a *analyzer) collectConstDeps(m, k string, e frontend.Expr, seen map[string]bool, deps *[]Dep) {
	switch t := e.(type) {
	case *frontend.IdentExpr:
		if rk, info, err := a.result.resolve(m, t.Name); err == nil && info.Kind == SymConst {
			if info.Pub && !seen[rk] {
				*deps = append(*deps, Dep{Kind: DepInlineConst, Target: rk})
			}
			seen[rk] = true
		}
	case *frontend.UnaryExpr:
		a.collectConstDeps(m, k, t.Inner, seen, deps)
	case *frontend.BinaryExpr:
		a.collectConstDeps(m, k, t.Lhs, seen, deps)
		a.collectConstDeps(m, k, t.Rhs, seen, deps)
	}
}

func funcSigText(info *SymbolInfo) string {
	ps := make([]string, len(info.Params))
	for i, p := range info.Params {
		ps[i] = p.Name + ":" + p.Type
	}
	g := ""
	if info.Generic {
		g = "<generic>"
	}
	return fmt.Sprintf("fn%s(%s)->%s", g, strings.Join(ps, ","), dash(info.Result))
}

func constSigText(info *SymbolInfo) string {
	return fmt.Sprintf("const:%s", info.ConstTy)
}

func dedupDeps(deps []Dep) []Dep {
	seen := map[string]bool{}
	out := make([]Dep, 0, len(deps))
	for _, d := range deps {
		kk := string(d.Kind) + "|" + d.Target
		if !seen[kk] {
			seen[kk] = true
			out = append(out, d)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Kind != out[j].Kind {
			return out[i].Kind < out[j].Kind
		}
		return out[i].Target < out[j].Target
	})
	return out
}

// ConstDigest renders the folded value of a constant symbol deterministically.
func (an *Analysis) ConstDigest(key string) string {
	v := an.ConstVal[key]
	if v == nil {
		return ""
	}
	switch v.ty {
	case "int":
		return fmt.Sprintf("int:%d", v.i)
	case "bool":
		return fmt.Sprintf("bool:%t", v.b)
	case "string":
		return "string:" + sha1short(v.s)
	}
	return ""
}
