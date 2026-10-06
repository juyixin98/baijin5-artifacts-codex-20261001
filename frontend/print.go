package frontend

import "strings"

// ExprString renders a guard expression in source form (for logs and traces).
func ExprString(e Expr) string {
	switch t := e.(type) {
	case ELit:
		return t.Val.String()
	case EVar:
		return t.Name
	case ECall:
		parts := make([]string, len(t.Args))
		for i, a := range t.Args {
			parts[i] = ExprString(a)
		}
		return t.Func + "(" + strings.Join(parts, ", ") + ")"
	case EAnd:
		return "(" + ExprString(t.L) + " and " + ExprString(t.R) + ")"
	case EOr:
		return "(" + ExprString(t.L) + " or " + ExprString(t.R) + ")"
	case ENot:
		return "not " + ExprString(t.E)
	}
	return "?"
}

// PatternString renders a pattern in source form.
func PatternString(p Pattern) string {
	switch t := p.(type) {
	case PWildcard:
		return "_"
	case PVar:
		return t.Name
	case PLit:
		return t.Val.String()
	case PCtor:
		if len(t.Args) == 0 {
			return t.Name
		}
		parts := make([]string, len(t.Args))
		for i, a := range t.Args {
			parts[i] = PatternString(a)
		}
		return t.Name + "(" + strings.Join(parts, ", ") + ")"
	}
	return "?"
}
