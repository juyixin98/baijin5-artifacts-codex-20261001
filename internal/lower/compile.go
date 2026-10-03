package lower

import (
	"fmt"
	"sort"
	"strings"

	"rlc/internal/frontend"
	"rlc/internal/ir"
)

// CompiledModule is the per-module codegen result. Instantiated generic
// specializations are collected globally because their key is independent of
// the caller module.
type CompiledModule struct {
	Module string
	Funcs  []*ir.Func
	Consts []*ir.Const
}

// Compile lowers the whole analyzed program and returns per-module functions,
// constants and all generic specializations required by call sites.
func Compile(an *Analysis) ([]*CompiledModule, []*ir.Func, error) {
	cg := &codegen{
		an:       an,
		specs:    map[string]*ir.Func{},
		queue:    map[string]bool{},
		specDeps: map[string][]Dep{},
	}
	var out []*CompiledModule
	for _, m := range an.Order {
		cm := &CompiledModule{Module: m}
		ma := an.Modules[m]
		for _, k := range ma.Order {
			info := an.Symbols[k]
			switch info.Kind {
			case SymFunc:
				if info.Generic {
					// Template is not directly executable; enqueue discovery
					// driven by call sites below.
					fn := cg.findFnDecl(m, info.Short)
					_ = fn
					continue
				}
				f, err := cg.compileFunc(k, info, map[string]string{}, nil)
				if err != nil {
					return nil, nil, err
				}
				cm.Funcs = append(cm.Funcs, f)
			case SymConst:
				v := an.ConstVal[k]
				cm.Consts = append(cm.Consts, &ir.Const{
					Name: k, Module: m, Short: info.Short, Pub: info.Pub,
					Type: info.ConstTy, Value: ir.Value{Type: v.ty, I: v.i, S: v.s, B: v.b},
				})
			}
		}
		out = append(out, cm)
	}
	if err := cg.discoverInstantiations(); err != nil {
		return nil, nil, err
	}
	if err := cg.processQueue(); err != nil {
		return nil, nil, err
	}
	keys := make([]string, 0, len(cg.specs))
	for k := range cg.specs {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	specList := make([]*ir.Func, 0, len(keys))
	for _, k := range keys {
		specList = append(specList, cg.specs[k])
	}
	return out, specList, nil
}

type codegen struct {
	an       *Analysis
	specs    map[string]*ir.Func
	queue    map[string]bool
	specDeps map[string][]Dep
}

type fnCompiler struct {
	cg     *codegen
	info   *SymbolInfo
	fn     *frontend.FnDecl
	subst  map[string]string
	locals map[string]int
	ltypes map[string]string
	nl     int
	code   []ir.Instr
}

func (cg *codegen) findFnDecl(mod, short string) *frontend.FnDecl {
	for _, d := range cg.an.Modules[mod].AST.Decls {
		if f, ok := d.(*frontend.FnDecl); ok && f.Name == short {
			return f
		}
	}
	return nil
}

func (cg *codegen) compileFunc(k string, info *SymbolInfo, subst map[string]string, extraDeps *[]Dep) (*ir.Func, error) {
	fn := cg.findFnDecl(info.Module, info.Short)
	fc := &fnCompiler{
		cg: cg, info: info, fn: fn, subst: subst,
		locals: map[string]int{}, ltypes: map[string]string{},
	}
	for _, p := range fn.Params {
		fc.local(p.Name)
		fc.ltypes[p.Name] = concrete(p.Type, subst)
	}
	for _, s := range fn.Body {
		if err := fc.stmt(s); err != nil {
			return nil, err
		}
	}
	if !endsWithReturn(fc.code) {
		fc.emit(ir.Instr{Op: ir.OpReturnVoid})
	}
	result := info.Result
	if result == "T" {
		result = subst["T"]
	}
	params := make([]string, len(info.Params))
	ptypes := make([]string, len(info.Params))
	for i, p := range info.Params {
		params[i] = p.Name
		ptypes[i] = concrete(p.Type, subst)
	}
	return &ir.Func{
		Name: k, Module: info.Module, Short: info.Short,
		Params: params, ParamTypes: ptypes, Result: result,
		Generic: false, Body: fc.code, NumLocals: fc.nl,
	}, nil
}

func (fc *fnCompiler) local(name string) int {
	if s, ok := fc.locals[name]; ok {
		return s
	}
	s := fc.nl
	fc.locals[name] = s
	fc.nl++
	return s
}

func (fc *fnCompiler) bindLocal(name, ty string) int {
	slot := fc.local(name)
	fc.ltypes[name] = ty
	return slot
}

func (fc *fnCompiler) emit(i ir.Instr) int {
	i.Line = 0
	fc.code = append(fc.code, i)
	return len(fc.code) - 1
}

func endsWithReturn(code []ir.Instr) bool {
	if len(code) == 0 {
		return false
	}
	last := code[len(code)-1].Op
	return last == ir.OpReturn || last == ir.OpReturnVoid
}

func (fc *fnCompiler) stmt(s frontend.Stmt) error {
	switch t := s.(type) {
	case *frontend.LetStmt:
		if err := fc.expr(t.Value); err != nil {
			return err
		}
		ty, _ := fc.inferType(t.Value)
		slot := fc.bindLocal(t.Name, ty)
		fc.emit(ir.Instr{Op: ir.OpStore, Slot: slot})
	case *frontend.ReturnStmt:
		if t.Has {
			if err := fc.expr(t.Value); err != nil {
				return err
			}
			fc.emit(ir.Instr{Op: ir.OpReturn})
		} else {
			fc.emit(ir.Instr{Op: ir.OpReturnVoid})
		}
	case *frontend.ExprStmt:
		if err := fc.expr(t.Expr); err != nil {
			return err
		}
		// discard expression result (e.g. calls used as statements)
	case *frontend.IfStmt:
		if err := fc.expr(t.Cond); err != nil {
			return err
		}
		jf := fc.emit(ir.Instr{Op: ir.OpJumpIfFalse})
		for _, s := range t.Then {
			if err := fc.stmt(s); err != nil {
				return err
			}
		}
		if len(t.Else) > 0 {
			jEnd := fc.emit(ir.Instr{Op: ir.OpJump})
			fc.code[jf].Jump = len(fc.code)
			for _, s := range t.Else {
				if err := fc.stmt(s); err != nil {
					return err
				}
			}
			fc.code[jEnd].Jump = len(fc.code)
		} else {
			fc.code[jf].Jump = len(fc.code)
		}
	}
	return nil
}

func (fc *fnCompiler) expr(e frontend.Expr) error {
	switch t := e.(type) {
	case *frontend.IntLit:
		fc.emit(ir.Instr{Op: ir.OpConstInt, Int: t.Value})
	case *frontend.StrLit:
		fc.emit(ir.Instr{Op: ir.OpConstStr, Str: t.Value})
	case *frontend.BoolLit:
		fc.emit(ir.Instr{Op: ir.OpConstBool, Bool: t.Value})
	case *frontend.IdentExpr:
		if slot, ok := fc.locals[t.Name]; ok {
			fc.emit(ir.Instr{Op: ir.OpLoad, Slot: slot})
			return nil
		}
		rk, info, err := fc.cg.an.resolve(fc.info.Module, t.Name)
		if err != nil {
			return err
		}
		if info.Kind != SymConst {
			return fmt.Errorf("codegen: %s is not a value", t.Name)
		}
		v := fc.cg.an.ConstVal[rk]
		switch v.ty {
		case "int":
			fc.emit(ir.Instr{Op: ir.OpConstInt, Int: v.i})
		case "string":
			fc.emit(ir.Instr{Op: ir.OpConstStr, Str: v.s})
		case "bool":
			fc.emit(ir.Instr{Op: ir.OpConstBool, Bool: v.b})
		}
	case *frontend.CallExpr:
		rk, info, err := fc.cg.an.resolve(fc.info.Module, t.Name)
		if err != nil {
			return err
		}
		argTys := []string{}
		// Emit arguments and recover their types via the AST typing pass data
		// is not retained; re-infer via a small local type inference.
		for _, ar := range t.Args {
			if err := fc.expr(ar); err != nil {
				return err
			}
			ty, err := fc.inferType(ar)
			if err != nil {
				return err
			}
			argTys = append(argTys, ty)
		}
		if info.Generic {
			tyArg := "int"
			for i, p := range info.Params {
				if p.Type == "T" {
					tyArg = argTys[i]
				}
			}
			sk := specKey(rk, tyArg)
			fc.cg.enqueue(sk, rk, tyArg)
			fc.emit(ir.Instr{Op: ir.OpGenericCall, Str: sk, Types: []string{tyArg}})
		} else {
			fc.emit(ir.Instr{Op: ir.OpCall, Str: rk})
		}
	case *frontend.UnaryExpr:
		if err := fc.expr(t.Inner); err != nil {
			return err
		}
		fc.emit(ir.Instr{Op: ir.OpNeg})
	case *frontend.BinaryExpr:
		if err := fc.expr(t.Lhs); err != nil {
			return err
		}
		if err := fc.expr(t.Rhs); err != nil {
			return err
		}
		fc.emit(ir.Instr{Op: ir.OpBin, Bin: binMap[t.Op]})
	}
	return nil
}

// inferType re-runs lightweight type inference for call arguments during
// codegen, reusing the analyzer's resolution tables.
func (fc *fnCompiler) inferType(e frontend.Expr) (string, error) {
	return fc.inferEnv(e, fc.ltypes)
}

func (fc *fnCompiler) inferEnv(e frontend.Expr, env map[string]string) (string, error) {
	an := fc.cg.an
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
		_, info, err := an.resolve(fc.info.Module, t.Name)
		if err != nil {
			return "", err
		}
		return info.ConstTy, nil
	case *frontend.BinaryExpr:
		if _, err := fc.inferEnv(t.Lhs, env); err != nil {
			return "", err
		}
		if _, err := fc.inferEnv(t.Rhs, env); err != nil {
			return "", err
		}
		switch t.Op {
		case "==", "!=", "<", "<=", ">", ">=":
			return "bool", nil
		default:
			return "int", nil
		}
	case *frontend.UnaryExpr:
		return "int", nil
	case *frontend.CallExpr:
		_, info, err := an.resolve(fc.info.Module, t.Name)
		if err != nil {
			return "", err
		}
		if info.Result == "T" {
			for i, p := range info.Params {
				if p.Type == "T" && i < len(t.Args) {
					return fc.inferEnv(t.Args[i], env)
				}
			}
		}
		return info.Result, nil
	}
	return "", fmt.Errorf("cannot infer type of %T", e)
}

var binMap = map[string]ir.BinOp{
	"+": ir.BinAdd, "-": ir.BinSub, "*": ir.BinMul, "/": ir.BinDiv, "%": ir.BinMod,
	"<": ir.BinLt, "<=": ir.BinLe, ">": ir.BinGt, ">=": ir.BinGe,
	"==": ir.BinEq, "!=": ir.BinNe,
}

func specKey(genericKey, tyArg string) string {
	return genericKey + "<" + tyArg + ">"
}

type specJob struct {
	specKey string
	genKey  string
	tyArg   string
}

func (cg *codegen) enqueue(sk, genKey, tyArg string) {
	if _, ok := cg.specs[sk]; ok {
		return
	}
	if cg.queue[sk] {
		return
	}
	cg.queue[sk] = true
	cg.specDeps[sk] = []Dep{{Kind: DepGenericBody, Target: genKey}}
}

func (cg *codegen) discoverInstantiations() error {
	// Queue entries are created during compile of normal functions.
	return nil
}

func (cg *codegen) processQueue() error {
	for {
		var pending []specJob
		for sk := range cg.queue {
			genKey := sk[:strings.LastIndex(sk, "<")]
			tyArg := sk[strings.LastIndex(sk, "<")+1 : len(sk)-1]
			pending = append(pending, specJob{sk, genKey, tyArg})
		}
		if len(pending) == 0 {
			return nil
		}
		sort.Slice(pending, func(i, j int) bool { return pending[i].specKey < pending[j].specKey })
		for _, j := range pending {
			delete(cg.queue, j.specKey)
			if _, ok := cg.specs[j.specKey]; ok {
				continue
			}
			info := cg.an.Symbols[j.genKey]
			f, err := cg.compileFunc(j.specKey, info, map[string]string{"T": j.tyArg}, nil)
			if err != nil {
				return err
			}
			cg.specs[j.specKey] = f
		}
	}
}
