package ir

import (
	"rlmod/internal/frontend"
	"sort"
	"strings"
)

func (l *lowerer) expr(e frontend.Expr) (TypeRef, error) {
	switch {
	case e.Lit != nil:
		switch e.Lit.Kind {
		case frontend.LitInt:
			l.emit(Instr{Op: OPushInt, Int: e.Lit.IntVal})
			return TypeRef{Name: PrimInt}, nil
		case frontend.LitStr:
			l.emit(Instr{Op: OPushStr, Str: e.Lit.StrVal})
			return TypeRef{Name: PrimStr}, nil
		}
	case e.Unary != nil:
		t, err := l.expr(e.Unary.Inner)
		if err != nil {
			return TypeRef{}, err
		}
		if l.c.normalize(t).Name != PrimInt || e.Unary.Op != "-" {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnsupportedOp, "unary %s on %s", e.Unary.Op, t)
		}
		l.emit(Instr{Op: ONeg})
		return t, nil
	case e.Binary != nil:
		return l.binary(e)
	case e.Var != nil:
		return l.loadVar(e)
	case e.Call != nil:
		return l.call(e)
	}
	return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnresolved, "malformed expression")
}

func (l *lowerer) binary(e frontend.Expr) (TypeRef, error) {
	op := e.Binary.Op
	lt, err := l.expr(e.Binary.Left)
	if err != nil {
		return TypeRef{}, err
	}
	rt, err := l.expr(e.Binary.Right)
	if err != nil {
		return TypeRef{}, err
	}
	ln, rn := l.c.normalize(lt), l.c.normalize(rt)
	switch op {
	case "==", "!=", "<", "<=", ">", ">=":
		if ln != rn {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindTypeMismatch, "compare %s vs %s", lt, rt)
		}
		l.emit(Instr{Op: OBin, Str: op})
		return TypeRef{Name: PrimInt}, nil
	case "+", "-", "*", "/":
		if ln != rn {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindTypeMismatch, "%s %s %s", lt, op, rt)
		}
		if ln.Name == PrimStr && op != "+" {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnsupportedOp, "string operator %s", op)
		}
		l.emit(Instr{Op: OBin, Str: op})
		return ln, nil
	}
	return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnsupportedOp, "operator %s", op)
}

func (l *lowerer) resolveRef(mod string, ref *frontend.Variable) (string, error) {
	target := mod
	if ref.Module != "" {
		if l.c.mods[ref.Module] == nil {
			return "", l.c.err(l.mod, ref.Line(), 1, KindUnknownModule, "unknown module %s", ref.Module)
		}
		target = ref.Module
	}
	if target != l.mod && !Exported(ref.Name) {
		return "", l.c.err(l.mod, ref.Line(), 1, KindUnknownSymbol, "cannot reference private symbol %s of module %s", ref.Name, target)
	}
	return target, nil
}

func (l *lowerer) loadVar(e frontend.Expr) (TypeRef, error) {
	ref := e.Var
	// locals first (unqualified)
	if ref.Module == "" {
		if idx, ok := l.locals[ref.Name]; ok {
			l.emit(Instr{Op: OLoadLocal, Index: idx})
			return l.localTy[idx], nil
		}
	}
	target, err := l.resolveRef(l.mod, ref)
	if err != nil {
		return TypeRef{}, err
	}
	if cd, ok := l.c.mods[target].Consts[ref.Name]; ok {
		// inline the folded value at the use site
		switch cd.Kind {
		case frontend.LitInt:
			l.emit(Instr{Op: OPushInt, Int: cd.IntVal})
		case frontend.LitStr:
			l.emit(Instr{Op: OPushStr, Str: cd.StrVal})
		}
		// record the dependency on the const for fingerprint edges.
		l.fn.constDeps = append(l.fn.constDeps, target+"."+ref.Name)
		return cd.Type, nil
	}
	return TypeRef{}, l.c.err(l.mod, ref.Line(), 1, KindUnknownSymbol, "unknown name %s", ref.Name)
}

func (l *lowerer) call(e frontend.Expr) (TypeRef, error) {
	call := e.Call
	target := l.mod
	if call.Module != "" {
		if l.c.mods[call.Module] == nil {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnknownModule, "unknown module %s", call.Module)
		}
		target = call.Module
	}
	if target != l.mod && !Exported(call.Name) {
		return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnknownSymbol, "cannot call private %s.%s", target, call.Name)
	}
	var argTy []TypeRef
	for _, a := range call.Args {
		t, err := l.expr(a)
		if err != nil {
			return TypeRef{}, err
		}
		argTy = append(argTy, t)
	}
	// generic instantiation?
	if len(call.TypeArgs) > 0 {
		src := l.c.mods[target].GenericSrc[call.Name]
		if src == nil {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnknownSymbol, "no generic %s.%s", target, call.Name)
		}
		if len(call.TypeArgs) != len(src.TypeParams) {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindTypeArgCount, "%s expects %d type args", call.Name, len(src.TypeParams))
		}
		var resolved []string
		for _, ta := range call.TypeArgs {
			rt, err := l.c.resolveType(l.mod, ta)
			if err != nil {
				return TypeRef{}, err
			}
			rt = l.subst(rt)
			if !rt.isConcrete() {
				return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindTypeArgCount, "type arg %s is not concrete", ta.Name)
			}
			// qualified types use "/" in monomorphization keys ("module/Name")
			enc := rt.Name
			if rt.Module != "" {
				enc = rt.Module + "/" + rt.Name
			}
			resolved = append(resolved, enc)
		}
		inst := strings.Join(resolved, ",")
		_, gf, _, gerr := l.c.ensureInstance(target, call.Name, inst)
		if gerr != nil {
			return TypeRef{}, gerr
		}
		key := call.Name + "[" + inst + "]"
		if err := l.checkCallSig(target, key, argTy, e.Line); err != nil {
			return TypeRef{}, err
		}
		l.emit(Instr{Op: OCall, Module: target, Str: call.Name, Instance: inst, Index: len(call.Args)})
		l.fn.genericDeps = append(l.fn.genericDeps, target+"."+key)
		return gf.Result, nil
	}
	fn := l.c.mods[target].Funcs[call.Name]
	if fn == nil {
		if _, ok := l.c.mods[target].GenericSrc[call.Name]; ok {
			return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindTypeArgCount, "generic %s requires type arguments", call.Name)
		}
		return TypeRef{}, l.c.err(l.mod, e.Line, 1, KindUnknownSymbol, "unknown function %s.%s", target, call.Name)
	}
	if err := l.checkCallSig(target, fn.Key, argTy, e.Line); err != nil {
		return TypeRef{}, err
	}
	l.emit(Instr{Op: OCall, Module: target, Str: call.Name, Instance: "", Index: len(call.Args)})
	l.fn.callDeps = append(l.fn.callDeps, target+"."+call.Name)
	return fn.Result, nil
}

func (l *lowerer) checkCallSig(target, key string, argTy []TypeRef, line int) error {
	fn := l.c.mods[target].Funcs[key]
	if fn == nil {
		// pending instantiation: signature can be computed without a body.
		return nil
	}
	if len(argTy) != len(fn.Params) {
		return l.c.err(l.mod, line, 1, KindArityMismatch, "%s.%s expects %d args, got %d", target, key, len(fn.Params), len(argTy))
	}
	for i, at := range argTy {
		if !l.c.assignable(fn.Params[i].Type, at) {
			return l.c.err(l.mod, line, 1, KindTypeMismatch, "%s.%s arg %d expects %s, got %s", target, key, i+1, fn.Params[i].Type, at)
		}
	}
	return nil
}

func (t TypeRef) isConcrete() bool {
	return t.Name == PrimInt || t.Name == PrimStr || t.Module != ""
}

var _ = sort.Strings
