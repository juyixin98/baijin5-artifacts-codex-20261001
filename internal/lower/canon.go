package lower

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"

	"rlc/internal/frontend"
)

// Canon renders an AST node as deterministic text used for body and template
// hashing. It is independent of source formatting and comments.
func Canon(n any) string {
	var b strings.Builder
	canonNode(&b, n)
	return b.String()
}

func canonNode(b *strings.Builder, n any) {
	switch t := n.(type) {
	case *frontend.FnDecl:
		fmt.Fprintf(b, "(fn%s%s %s (", boolTag(t.Pub), genTag(t.Generic), t.Name)
		for i, p := range t.Params {
			if i > 0 {
				b.WriteByte(' ')
			}
			fmt.Fprintf(b, "(%s %s)", p.Name, p.Type)
		}
		fmt.Fprintf(b, ") -> %s ", dash(t.Result))
		canonBlock(b, t.Body)
		b.WriteByte(')')
	case *frontend.ConstDecl:
		fmt.Fprintf(b, "(const%s %s %s ", boolTag(t.Pub), t.Name, t.Type)
		canonNode(b, t.Value)
		b.WriteByte(')')
	case *frontend.LetStmt:
		fmt.Fprintf(b, "(let %s %s ", t.Name, dash(t.Type))
		canonNode(b, t.Value)
		b.WriteByte(')')
	case *frontend.ReturnStmt:
		if t.Has {
			b.WriteString("(return ")
			canonNode(b, t.Value)
			b.WriteByte(')')
		} else {
			b.WriteString("(return)")
		}
	case *frontend.ExprStmt:
		b.WriteString("(expr ")
		canonNode(b, t.Expr)
		b.WriteByte(')')
	case *frontend.IfStmt:
		b.WriteString("(if ")
		canonNode(b, t.Cond)
		b.WriteByte(' ')
		canonBlock(b, t.Then)
		b.WriteByte(' ')
		canonBlock(b, t.Else)
		b.WriteByte(')')
	case *frontend.IntLit:
		fmt.Fprintf(b, "(int %d)", t.Value)
	case *frontend.StrLit:
		fmt.Fprintf(b, "(str %q)", t.Value)
	case *frontend.BoolLit:
		fmt.Fprintf(b, "(bool %t)", t.Value)
	case *frontend.IdentExpr:
		fmt.Fprintf(b, "(id %s)", t.Name)
	case *frontend.CallExpr:
		fmt.Fprintf(b, "(call %s", t.Name)
		for _, a := range t.Args {
			b.WriteByte(' ')
			canonNode(b, a)
		}
		b.WriteString("))")
	case *frontend.UnaryExpr:
		fmt.Fprintf(b, "(un %s ", t.Op)
		canonNode(b, t.Inner)
		b.WriteByte(')')
	case *frontend.BinaryExpr:
		fmt.Fprintf(b, "(bin %s ", t.Op)
		canonNode(b, t.Lhs)
		b.WriteByte(' ')
		canonNode(b, t.Rhs)
		b.WriteByte(')')
	default:
		fmt.Fprintf(b, "(unknown %T)", n)
	}
}

func canonBlock(b *strings.Builder, stmts []frontend.Stmt) {
	b.WriteByte('(')
	for i, s := range stmts {
		if i > 0 {
			b.WriteByte(' ')
		}
		canonNode(b, s)
	}
	b.WriteByte(')')
}

func boolTag(v bool) string {
	if v {
		return ":pub"
	}
	return ""
}

func genTag(v bool) string {
	if v {
		return ":generic"
	}
	return ""
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func sha1short(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])[:16]
}
