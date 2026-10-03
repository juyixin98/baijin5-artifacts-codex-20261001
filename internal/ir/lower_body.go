package ir

import (
	"rlmod/internal/frontend"
)

type lowerer struct {
	c          *compiler
	mod        string
	fn         *Func
	tparams    map[string]TypeRef
	locals     map[string]int
	localTy    []TypeRef
	instrs     []Instr
	needReturn bool
}

func (c *compiler) lowerBody(mod string, f *Func, bind map[string]TypeRef, decl *frontend.FuncDecl) error {
	l := &lowerer{
		c:       c,
		mod:     mod,
		fn:      f,
		tparams: map[string]TypeRef{},
		locals:  map[string]int{},
	}
	for k, v := range bind {
		l.tparams[k] = v
	}
	// parameters occupy locals 0..n-1
	for _, p := range decl.Params {
		tr, err := c.resolveType(mod, p.Type)
		if err != nil {
			return err
		}
		tr = l.subst(tr)
		l.locals[p.Name] = len(l.localTy)
		l.localTy = append(l.localTy, tr)
	}
	// signature fields
	f.Params = make([]Param, 0, len(decl.Params))
	for _, p := range decl.Params {
		tr, _ := c.resolveType(mod, p.Type)
		f.Params = append(f.Params, Param{Name: p.Name, Type: l.subst(tr)})
	}
	if decl.Result.Name != "" {
		rt, err := c.resolveType(mod, decl.Result)
		if err != nil {
			return err
		}
		f.HasResult = true
		f.Result = l.subst(rt)
	}
	for _, st := range decl.Body {
		if err := l.stmt(st); err != nil {
			return err
		}
	}
	if !l.needReturn {
		l.emit(Instr{Op: OReturn, Index: 0})
	}
	f.LocalTypes = l.localTy
	f.Instrs = l.instrs
	l.patchJumps()
	return nil
}

func (l *lowerer) subst(t TypeRef) TypeRef {
	if t.Module == "" {
		if b, ok := l.tparams[t.Name]; ok {
			return b
		}
	}
	return t
}

func (l *lowerer) emit(in Instr) int {
	idx := len(l.instrs)
	l.instrs = append(l.instrs, in)
	return idx
}

func (l *lowerer) stmt(s frontend.Stmt) error {
	switch {
	case s.Var != nil:
		var ty TypeRef
		if s.Var.HasInit {
			ety, err := l.expr(*s.Var.Init)
			if err != nil {
				return err
			}
			ty = ety
		}
		if s.Var.Type.Name != "" {
			rt, err := l.c.resolveType(l.mod, s.Var.Type)
			if err != nil {
				return err
			}
			rt = l.subst(rt)
			if s.Var.HasInit && !l.c.assignable(rt, ty) {
				return l.c.err(l.mod, s.Line, 1, KindTypeMismatch, "cannot initialize %s with %s", rt, ty)
			}
			ty = rt
		}
		if !s.Var.HasInit {
			// zero value by primitive kind
			l.emitZero(ty)
		}
		idx, exists := l.locals[s.Var.Name]
		if !exists {
			idx = len(l.localTy)
			l.locals[s.Var.Name] = idx
			l.localTy = append(l.localTy, ty)
		} else {
			l.localTy[idx] = ty
		}
		l.emit(Instr{Op: OStoreLocal, Index: idx})
	case s.Return != nil:
		if s.Return.Value != nil {
			ty, err := l.expr(*s.Return.Value)
			if err != nil {
				return err
			}
			if !l.fn.HasResult {
				return l.c.err(l.mod, s.Line, 1, KindTypeMismatch, "function has no result")
			}
			if !l.c.assignable(l.fn.Result, ty) {
				return l.c.err(l.mod, s.Line, 1, KindTypeMismatch, "return type %s, got %s", l.fn.Result, ty)
			}
			l.emit(Instr{Op: OReturn, Index: 1})
		} else {
			if l.fn.HasResult {
				return l.c.err(l.mod, s.Line, 1, KindTypeMismatch, "missing return value")
			}
			l.emit(Instr{Op: OReturn, Index: 0})
		}
		l.needReturn = true
	case s.If != nil:
		if err := l.exprCond(s.If.Cond, s.Line); err != nil {
			return err
		}
		jf := l.emit(Instr{Op: OJumpIfFalse, Jump: -1})
		for _, st := range s.If.Then {
			if err := l.stmt(st); err != nil {
				return err
			}
		}
		jend := -1
		if len(s.If.Else) > 0 {
			jend = l.emit(Instr{Op: OJump, Jump: -1})
		}
		l.instrs[jf].Jump = len(l.instrs)
		for _, st := range s.If.Else {
			if err := l.stmt(st); err != nil {
				return err
			}
		}
		if jend >= 0 {
			l.instrs[jend].Jump = len(l.instrs)
		}
	case s.Expr.Value.Lit != nil || s.Expr.Value.Var != nil || s.Expr.Value.Call != nil || s.Expr.Value.Unary != nil || s.Expr.Value.Binary != nil:
		if _, err := l.expr(s.Expr.Value); err != nil {
			return err
		}
		l.emit(Instr{Op: OPop})
	default:
		return l.c.err(l.mod, s.Line, 1, KindUnresolved, "empty statement")
	}
	return nil
}

func (l *lowerer) patchJumps() {}

func (l *lowerer) emitZero(t TypeRef) {
	switch l.c.normalize(t).Name {
	case PrimStr:
		l.emit(Instr{Op: OPushStr, Str: ""})
	default:
		l.emit(Instr{Op: OPushInt, Int: 0})
	}
}

func (l *lowerer) exprCond(e frontend.Expr, line int) error {
	if _, err := l.expr(e); err != nil {
		return err
	}
	return nil
}
