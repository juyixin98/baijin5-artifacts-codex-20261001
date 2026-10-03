package ir

import (
	"fmt"
	"rlmod/internal/frontend"
	"sort"
)

// CompileError is a semantic (name resolution / type) error with position.
type CompileError struct {
	Module string
	Line   int
	Col    int
	Kind   ErrorKind
	Msg    string
}

// ErrorKind classifies compilation failures for precise test assertions.
type ErrorKind string

const (
	KindUnknownModule  ErrorKind = "unknown_module"
	KindUnknownSymbol  ErrorKind = "unknown_symbol"
	KindUnknownType    ErrorKind = "unknown_type"
	KindTypeMismatch   ErrorKind = "type_mismatch"
	KindArityMismatch  ErrorKind = "arity_mismatch"
	KindTypeArgCount   ErrorKind = "typearg_count"
	KindUnsupportedOp  ErrorKind = "unsupported_operator"
	KindConstCycle     ErrorKind = "const_cycle"
	KindConstTypeError ErrorKind = "const_type_error"
	KindDuplicateDecl  ErrorKind = "duplicate_declaration"
	KindUnresolved     ErrorKind = "unresolved"
	KindImportMissing  ErrorKind = "import_missing"
)

func (e *CompileError) Error() string {
	return fmt.Sprintf("compile[%s] %s at %s:%d:%d", e.Kind, e.Msg, e.Module, e.Line, e.Col)
}

type constVal struct {
	kind frontend.LitKind
	i    int64
	s    string
}

type compiler struct {
	parsed  map[string]*frontend.Program
	mods    map[string]*Module
	current string
	// generic instances pending/created: "module.Name[a,b]" -> true
	pending   []string
	done      map[string]bool
	foldVisit func(m, n string) (*constVal, TypeRef, error)
}

// Build lowers parsed modules into a linked Program. semver and schema are
// recorded on the program for downstream fingerprint/version gating.
func Build(parsed map[string]*frontend.Program, order []string, semver, schema string) (*Program, error) {
	c := &compiler{
		parsed: parsed,
		mods:   map[string]*Module{},
		done:   map[string]bool{},
	}
	for _, m := range order {
		prog := parsed[m]
		if prog == nil {
			return nil, &CompileError{Kind: KindImportMissing, Module: m, Msg: "module source missing"}
		}
		mod := &Module{
			Name:       m,
			Imports:    append([]string(nil), prog.Imports...),
			Types:      map[string]*TypeDecl{},
			Consts:     map[string]*Const{},
			Funcs:      map[string]*Func{},
			GenericSrc: map[string]*GenericSource{},
		}
		c.mods[m] = mod
	}
	if err := c.collect(); err != nil {
		return nil, err
	}
	if err := c.foldConsts(); err != nil {
		return nil, err
	}
	if err := c.lowerAll(); err != nil {
		return nil, err
	}
	return &Program{SchemaVersion: schema, SemVer: semver, Order: append([]string(nil), order...), Modules: c.mods}, nil
}

func (c *compiler) collect() error {
	for _, m := range c.modOrder() {
		prog := c.parsed[m]
		for _, imp := range prog.Imports {
			if _, ok := c.mods[imp]; !ok {
				return &CompileError{Kind: KindImportMissing, Module: m, Line: 1, Col: 1, Msg: "imported module not present: " + imp}
			}
		}
		for _, cl := range prog.Clauses {
			switch {
			case cl.Type != nil:
				d := cl.Type
				if _, dup := c.mods[m].Types[d.Name]; dup {
					return c.err(m, cl.Line, 1, KindDuplicateDecl, "duplicate type %s", d.Name)
				}
				base, err := c.resolveType(m, d.Base)
				if err != nil {
					return err
				}
				if base.Module != "" {
					return c.err(m, cl.Line, 1, KindUnknownType, "type base must be primitive or local named type: %s", d.Base.Name)
				}
				if base.Name != PrimInt && base.Name != PrimStr {
					if _, ok := c.mods[m].Types[base.Name]; !ok {
						return c.err(m, cl.Line, 1, KindUnknownType, "unknown base type %s", base.Name)
					}
				}
				c.mods[m].Types[d.Name] = &TypeDecl{Module: m, Name: d.Name, Base: base}
			case cl.Const != nil:
				d := cl.Const
				if _, dup := c.mods[m].Consts[d.Name]; dup {
					return c.err(m, cl.Line, 1, KindDuplicateDecl, "duplicate const %s", d.Name)
				}
				c.mods[m].Consts[d.Name] = &Const{Module: m, Name: d.Name, Sensitive: cl.Sensitive}
			case cl.Func != nil:
				d := cl.Func
				if len(d.TypeParams) > 0 {
					if _, dup := c.mods[m].GenericSrc[d.Name]; dup {
						return c.err(m, cl.Line, 1, KindDuplicateDecl, "duplicate generic %s", d.Name)
					}
					c.mods[m].GenericSrc[d.Name] = &GenericSource{Module: m, Name: d.Name, TypeParams: d.TypeParams, Decl: d}
				} else {
					if _, dup := c.mods[m].Funcs[d.Name]; dup {
						return c.err(m, cl.Line, 1, KindDuplicateDecl, "duplicate func %s", d.Name)
					}
					res, err := c.sigResult(m, d)
					if err != nil {
						return err
					}
					c.mods[m].Funcs[d.Name] = &Func{
						Module: m, OrigName: d.Name, Key: d.Name,
						Params: c.sigParams(m, d), HasResult: d.Result.Name != "", Result: res, Line: cl.Line,
					}
				}
			}
		}
	}
	return nil
}

func (c *compiler) sigParams(m string, d *frontend.FuncDecl) []Param {
	ps := make([]Param, 0, len(d.Params))
	for _, p := range d.Params {
		tr, _ := c.resolveType(m, p.Type)
		ps = append(ps, Param{Name: p.Name, Type: tr})
	}
	return ps
}

func (c *compiler) sigResult(m string, d *frontend.FuncDecl) (TypeRef, error) {
	if d.Result.Name == "" {
		return TypeRef{}, nil
	}
	return c.resolveType(m, d.Result)
}

func (c *compiler) modOrder() []string {
	names := make([]string, 0, len(c.mods))
	for name := range c.mods {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func sortedModuleNames(mods map[string]*Module) []string {
	names := make([]string, 0, len(mods))
	for name := range mods {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func sortedConstNames(cs map[string]*Const) []string {
	names := make([]string, 0, len(cs))
	for name := range cs {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func (c *compiler) constLine(m, n string) int {
	for _, cl := range c.parsed[m].Clauses {
		if cl.Const != nil && cl.Const.Name == n {
			return cl.Line
		}
	}
	return 0
}

func (c *compiler) err(m string, line, col int, kind ErrorKind, format string, args ...any) error {
	return &CompileError{Module: m, Line: line, Col: col, Kind: kind, Msg: fmt.Sprintf(format, args...)}
}

func (c *compiler) resolveType(m string, ref frontend.TypeRef) (TypeRef, error) {
	if ref.Name == PrimInt || ref.Name == PrimStr {
		if ref.Module != "" {
			return TypeRef{}, c.err(m, 0, 0, KindUnknownType, "primitive %s cannot be qualified", ref.Name)
		}
		return TypeRef{Name: ref.Name}, nil
	}
	if ref.Module != "" {
		mod := c.mods[ref.Module]
		if mod == nil {
			return TypeRef{}, c.err(m, 0, 0, KindUnknownModule, "unknown module %s", ref.Module)
		}
		if _, ok := mod.Types[ref.Name]; !ok {
			return TypeRef{}, c.err(m, 0, 0, KindUnknownType, "%s has no type %s", ref.Module, ref.Name)
		}
		return TypeRef{Module: ref.Module, Name: ref.Name}, nil
	}
	if _, ok := c.mods[m].Types[ref.Name]; ok {
		return TypeRef{Module: m, Name: ref.Name}, nil
	}
	// allow references resolved later: generic type parameters handled by caller
	return TypeRef{Name: ref.Name}, nil
}

// --- constant folding -------------------------------------------------------

func (c *compiler) foldConsts() error {
	type job struct{ m, n string }
	state := map[string]int{} // 0 unseen 1 active 2 done
	var key = func(m, n string) string { return m + "." + n }
	var visit func(m, n string) (*constVal, TypeRef, error)
	visit = func(m, n string) (*constVal, TypeRef, error) {
		k := key(m, n)
		switch state[k] {
		case 1:
			return nil, TypeRef{}, c.err(m, 0, 0, KindConstCycle, "constant cycle through %s", k)
		case 2:
			cd := c.mods[m].Consts[n]
			return &constVal{kind: cd.Kind, i: cd.IntVal, s: cd.StrVal}, cd.Type, nil
		}
		state[k] = 1
		decl := c.constDecl(m, n)
		if decl == nil {
			return nil, TypeRef{}, c.err(m, 0, 0, KindUnknownSymbol, "unknown const %s", n)
		}
		v, typ, err := c.evalConstExpr(m, decl.Init)
		if err != nil {
			return nil, TypeRef{}, err
		}
		state[k] = 2
		cd := c.mods[m].Consts[n]
		if decl.Type.Name != "" {
			want, err := c.resolveType(m, decl.Type)
			if err != nil {
				return nil, TypeRef{}, err
			}
			if !c.assignable(want, typ) {
				return nil, TypeRef{}, c.err(m, c.constLine(m, n), 1, KindConstTypeError, "const %s declared %s but initialized with %s", n, want, typ)
			}
			typ = want
		}
		cd.Type = typ
		cd.Kind = v.kind
		cd.IntVal = v.i
		cd.StrVal = v.s
		return v, typ, nil
	}
	c.foldVisit = visit
	for _, m := range c.modOrder() {
		for _, n := range sortedConstNames(c.mods[m].Consts) {
			if _, _, err := visit(m, n); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c *compiler) constDecl(m, n string) *frontend.ConstDecl {
	for _, cl := range c.parsed[m].Clauses {
		if cl.Const != nil && cl.Const.Name == n {
			return cl.Const
		}
	}
	return nil
}

func (c *compiler) evalConstExpr(m string, e frontend.Expr) (*constVal, TypeRef, error) {
	switch {
	case e.Lit != nil:
		switch e.Lit.Kind {
		case frontend.LitInt:
			return &constVal{kind: frontend.LitInt, i: e.Lit.IntVal}, TypeRef{Name: PrimInt}, nil
		case frontend.LitStr:
			return &constVal{kind: frontend.LitStr, s: e.Lit.StrVal}, TypeRef{Name: PrimStr}, nil
		}
	case e.Unary != nil:
		v, t, err := c.evalConstExpr(m, e.Unary.Inner)
		if err != nil {
			return nil, TypeRef{}, err
		}
		if t.Name != PrimInt || e.Unary.Op != "-" {
			return nil, TypeRef{}, c.err(m, e.Line, 1, KindConstTypeError, "const unary %s unsupported on %s", e.Unary.Op, t)
		}
		return &constVal{kind: frontend.LitInt, i: -v.i}, t, nil
	case e.Binary != nil:
		lv, lt, err := c.evalConstExpr(m, e.Binary.Left)
		if err != nil {
			return nil, TypeRef{}, err
		}
		rv, rt, err := c.evalConstExpr(m, e.Binary.Right)
		if err != nil {
			return nil, TypeRef{}, err
		}
		if lt.Name != rt.Name {
			return nil, TypeRef{}, c.err(m, e.Line, 1, KindConstTypeError, "const binary operand mismatch %s %s %s", lt, e.Binary.Op, rt)
		}
		switch lt.Name {
		case PrimInt:
			r, err := intOp(e.Binary.Op, lv.i, rv.i)
			if err != nil {
				return nil, TypeRef{}, c.err(m, e.Line, 1, KindConstTypeError, err.Error())
			}
			return &constVal{kind: frontend.LitInt, i: r}, lt, nil
		case PrimStr:
			if e.Binary.Op != "+" {
				return nil, TypeRef{}, c.err(m, e.Line, 1, KindConstTypeError, "const string operator %s unsupported", e.Binary.Op)
			}
			return &constVal{kind: frontend.LitStr, s: lv.s + rv.s}, lt, nil
		}
	case e.Var != nil:
		mod := m
		if e.Var.Module != "" {
			mod = e.Var.Module
		}
		if c.mods[mod] == nil {
			return nil, TypeRef{}, c.err(m, e.Line, 1, KindUnknownModule, "unknown module %s in const reference", mod)
		}
		if _, ok := c.mods[mod].Consts[e.Var.Name]; ok {
			if c.foldVisit == nil {
				return nil, TypeRef{}, c.err(m, e.Line, 1, KindConstCycle, "constant reference outside folding")
			}
			return c.foldVisit(mod, e.Var.Name)
		}
		return nil, TypeRef{}, c.err(m, e.Line, 1, KindUnknownSymbol, "unknown constant %s.%s in const expression", mod, e.Var.Name)
	}
	return nil, TypeRef{}, c.err(m, e.Line, 1, KindConstTypeError, "expression is not constant")
}

func intOp(op string, a, b int64) (int64, error) {
	switch op {
	case "+":
		return a + b, nil
	case "-":
		return a - b, nil
	case "*":
		return a * b, nil
	case "/":
		if b == 0 {
			return 0, fmt.Errorf("division by zero in constant expression")
		}
		return a / b, nil
	default:
		return 0, fmt.Errorf("operator %s unsupported on int constants", op)
	}
}

// --- normalization / assignability -----------------------------------------

// normalize resolves a named type to its primitive base.
func (c *compiler) normalize(t TypeRef) TypeRef {
	if t.Name == PrimInt || t.Name == PrimStr {
		return TypeRef{Name: t.Name}
	}
	if t.Module != "" {
		if td, ok := c.mods[t.Module].Types[t.Name]; ok {
			return c.normalize(td.Base)
		}
	}
	return t
}

// assignable reports whether src may be passed where dst is expected. Named
// types are transparent over their primitive base in this restricted version;
// changing a declared base still changes the public fingerprint.
func (c *compiler) assignable(dst, src TypeRef) bool {
	if dst == src {
		return true
	}
	d := c.normalize(dst)
	s := c.normalize(src)
	return d == s
}
