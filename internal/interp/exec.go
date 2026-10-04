package interp

import (
	"genfsm/internal/ast"
	"genfsm/internal/value"
)

// flowKind identifies a non-local control transfer carried by flowPanic.
type flowKind int

const (
	flowReturn flowKind = iota
	flowThrow
)

// flowPanic propagates return/throw through Go call frames. finally clauses
// recover it, run their cleanup and re-raise (possibly replacing it).
type flowPanic struct {
	kind flowKind
	ret  value.Value
	ex   excVal
}

func (in *Interpreter) execBlock(b *ast.Block, parent *scope) {
	env := newScope(parent)
	for _, s := range b.Stmts {
		in.execStmt(s, env)
	}
}

// execFuncBody runs a function body and converts the terminal flowPanic into
// a (value, exception) pair.
func (in *Interpreter) execFuncBody(b *ast.Block, env *scope) (v value.Value, ex excVal) {
	defer func() {
		if r := recover(); r != nil {
			fp, ok := r.(*flowPanic)
			if !ok {
				panic(r)
			}
			if fp.kind == flowReturn {
				v, ex = fp.ret, excVal{}
				return
			}
			v, ex = value.NullV(), fp.ex
		}
	}()
	in.execBlock(b, env)
	return value.NullV(), excVal{}
}

func (in *Interpreter) execStmt(s ast.Stmt, env *scope) {
	switch n := s.(type) {
	case *ast.LetStmt:
		v := value.NullV()
		if n.Init != nil {
			v = in.must(n.Init, env)
		}
		env.define(n.Name, v)
	case *ast.AssignStmt:
		v := in.must(n.Value, env)
		name := n.Target.(*ast.NameExpr).Name
		p, ok := env.lookup(name)
		if !ok {
			panic(&flowPanic{kind: flowThrow, ex: exf("NameError", "undefined variable "+name)})
		}
		*p = v
	case *ast.ExprStmt:
		in.must(n.X, env)
	case *ast.IfStmt:
		if in.must(n.Cond, env).IsTruthy() {
			in.execBlock(n.Then, env)
		} else {
			switch e := n.Else.(type) {
			case *ast.Block:
				in.execBlock(e, env)
			case *ast.IfStmt:
				in.execStmt(e, env)
			}
		}
	case *ast.WhileStmt:
		for in.must(n.Cond, env).IsTruthy() {
			in.execBlock(n.Body, env)
		}
	case *ast.ForStmt:
		in.execFor(n, env)
	case *ast.ReturnStmt:
		v := value.NullV()
		if n.Value != nil {
			v = in.must(n.Value, env)
		}
		panic(&flowPanic{kind: flowReturn, ret: v})
	case *ast.ThrowStmt:
		v := in.must(n.Value, env)
		if v.Tag != value.Exception {
			panic(&flowPanic{kind: flowThrow, ex: exf("ThrowError", "throw requires an exception value")})
		}
		panic(&flowPanic{kind: flowThrow, ex: excVal{name: v.ExName, msg: v.ExMessage}})
	case *ast.TryStmt:
		in.execTry(n, env)
	}
}

// must evaluates e or converts an evaluation exception into a flowPanic.
func (in *Interpreter) must(e ast.Expr, env *scope) value.Value {
	v, ex := in.eval(e, env)
	if ex.Ok() {
		panic(&flowPanic{kind: flowThrow, ex: ex})
	}
	return v
}

// execTry implements try/catch/finally with explicit recovery of flowPanic.
// The finally block runs exactly once on every completion path; a flowPanic
// raised in catch/finally replaces any pending one.
func (in *Interpreter) execTry(n *ast.TryStmt, env *scope) {
	var pending *flowPanic

	catchPanic := func(r any) *flowPanic {
		fp, ok := r.(*flowPanic)
		if !ok {
			panic(r)
		}
		return fp
	}

	func() {
		defer func() {
			if r := recover(); r != nil {
				pending = catchPanic(r)
			}
		}()
		in.execBlock(n.Body, env)
	}()

	if pending != nil && pending.kind == flowThrow && n.Catch != nil {
		caughtEx := pending.ex
		consumed := false
		var repl *flowPanic
		func() {
			defer func() {
				if r := recover(); r != nil {
					repl = catchPanic(r)
					return
				}
				consumed = true
			}()
			cenv := newScope(env)
			cenv.define(n.Catch.Param, value.ExV(caughtEx.name, caughtEx.msg))
			in.execBlock(n.Catch.Body, cenv)
		}()
		if consumed {
			pending = nil
		} else if repl != nil {
			pending = repl
		}
	}

	if n.Finally != nil {
		var repl *flowPanic
		func() {
			defer func() {
				if r := recover(); r != nil {
					repl = catchPanic(r)
					return
				}
			}()
			in.execBlock(n.Finally, env)
		}()
		if repl != nil {
			pending = repl
		}
	}

	if pending != nil {
		panic(pending)
	}
}
