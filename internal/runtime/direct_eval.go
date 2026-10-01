package runtime

import (
	"strings"

	"genstatemachine/internal/gerr"

	"genstatemachine/internal/frontend"
)

func (env *dEnv) execList(list []frontend.Stmt) {
	for _, s := range list {
		env.exec(s)
	}
}

func (env *dEnv) exec(s frontend.Stmt) {
	switch x := s.(type) {
	case *frontend.BlockStmt:
		env.execList(x.List)

	case *frontend.VarStmt:
		if x.Init != nil {
			env.locals[x.Name] = env.eval(x.Init)
		} else {
			env.locals[x.Name] = NilVal()
		}

	case *frontend.AssignStmt:
		env.locals[x.Name] = env.eval(x.Expr)

	case *frontend.ExprStmt:
		_ = env.eval(x.Expr)

	case *frontend.YieldStmt:
		v := NilVal()
		if x.Expr != nil {
			v = env.eval(x.Expr)
		}

		req := env.parkAtYield(v)
		switch req.kind {
		case reqNext:
			// continue
		case reqThrow:
			panic(dRaised{value: req.inject})
		case reqClose:
			panic(sigClosing{})
		}

	case *frontend.ReturnStmt:
		r := sigReturn{}
		if x.Expr != nil {
			r.has = true
			r.value = env.eval(x.Expr)
		}
		panic(r)

	case *frontend.ThrowStmt:
		v := env.eval(x.Expr)
		if v.Kind != VStr {
			panic(dFailure{code: "type_mismatch", msg: "throw requires a string"})
		}
		panic(dRaised{value: v})

	case *frontend.IfStmt:
		if env.eval(x.Cond).Truthy() {
			env.execList(x.Then)
		} else {
			env.execList(x.Else)
		}

	case *frontend.WhileStmt:
		for env.eval(x.Cond).Truthy() {
			env.execList(x.Body)
		}

	case *frontend.TryStmt:
		env.execTry(x)
	}
}

// execTry realizes exact finally semantics with nested defers.
func (env *dEnv) execTry(x *frontend.TryStmt) {
	if len(x.Finally) > 0 {
		// Outer defer runs the cleanup for ANY abrupt (return/raise/close/
		// failure), including those raised inside the catch body.
		defer func() {
			env.execList(x.Finally)
		}()
	}

	if len(x.Catches) == 0 {
		env.execList(x.Body)
		return
	}

	clause := x.Catches[0]
	func() {
		defer func() {
			r := recover()
			if r == nil {
				return
			}
			raised, ok := r.(dRaised)
			if !ok {
				panic(r) // closing/return/failure/budget bypass catch
			}
			if clause.Test != nil {
				filter := env.eval(clause.Test)
				if !filter.Equals(raised.value) {
					panic(r)
				}
			}
			env.locals[clause.Name] = raised.value
			env.execList(clause.Body)
		}()
		env.execList(x.Body)
	}()
}

func (env *dEnv) eval(e frontend.Expr) Value {
	switch x := e.(type) {
	case *frontend.IntLit:
		return IntVal(x.Value)
	case *frontend.StrLit:
		return StrVal(x.Value)
	case *frontend.BoolLit:
		return BoolVal(x.Value)
	case *frontend.NilLit:
		return NilVal()
	case *frontend.Ident:
		v, ok := env.locals[x.Name]
		if !ok {
			panic(dFailure{code: "type_mismatch", msg: "read of undeclared variable " + x.Name})
		}
		return v
	case *frontend.UnaryExpr:
		v, err := evalUnary(x.Op, env.eval(x.Expr))
		if err != nil {
			panicFailure(err)
		}
		return v
	case *frontend.BinaryExpr:
		// Match the eager CFG semantics.
		l := env.eval(x.LHS)
		r := env.eval(x.RHS)
		v, err := evalBinary(x.Op, l, r)
		if err != nil {
			panicFailure(err)
		}
		return v
	case *frontend.CallExpr:
		if x.Name != "log" {
			panic(dFailure{code: "validation_error", msg: "unknown builtin " + x.Name})
		}
		parts := make([]string, 0, len(x.Args))
		for _, a := range x.Args {
			parts = append(parts, env.eval(a).Display())
		}
		line := strings.Join(parts, " ")
		env.logs = append(env.logs, line)
		return NilVal()
	}
	panic(dFailure{code: "uncaught_exit", msg: "unknown expression"})
}

func panicFailure(err error) {
	if ge, ok := gerr.As(err); ok {
		panic(dFailure{code: string(ge.Code), msg: ge.Error()})
	}
	panic(dFailure{code: "type_mismatch", msg: err.Error()})
}
