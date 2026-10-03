package fingerprint

import (
	"sort"
	"strconv"
	"strings"

	"rlmod/internal/ir"
)

// CanonicalBody serializes the executable body. The body enters the hash so
// generic instantiation bodies are explicit interface dependencies; a
// non-exported function's body hash is excluded from the module public hash.
func CanonicalBody(f *ir.Func) string {
	var sb strings.Builder
	for i, in := range f.Instrs {
		sb.WriteString(strconv.Itoa(i))
		sb.WriteString(":")
		sb.WriteString(OpName(in.Op))
		var fields []string
		switch in.Op {
		case ir.OPushInt:
			fields = append(fields, "i="+strconv.FormatInt(in.Int, 10))
		case ir.OPushStr:
			fields = append(fields, "q="+in.Str)
		case ir.OLoadLocal, ir.OStoreLocal:
			fields = append(fields, "n="+strconv.Itoa(in.Index))
		case ir.OCall:
			fields = append(fields, "m="+in.Module, "c="+in.Str)
			if in.Instance != "" {
				fields = append(fields, "g="+in.Instance)
			}
			fields = append(fields, "arity="+strconv.Itoa(in.Index))
		case ir.OBin:
			fields = append(fields, "op="+in.Str)
		case ir.OJump, ir.OJumpIfFalse:
			fields = append(fields, "j="+strconv.Itoa(in.Jump))
		case ir.OReturn:
			fields = append(fields, "has="+strconv.Itoa(in.Index))
		}
		if len(fields) > 0 {
			sb.WriteString("|")
			sb.WriteString(strings.Join(fields, ","))
		}
		sb.WriteString("\n")
	}
	return sb.String()
}

// OpName gives stable opcode names.
func OpName(op ir.Op) string {
	names := [...]string{
		"PushInt", "PushStr", "LoadLocal", "StoreLocal", "LoadConst",
		"Call", "Neg", "Bin", "Jump", "JumpIfFalse", "Return", "Pop",
	}
	if int(op) >= 0 && int(op) < len(names) {
		return names[op]
	}
	return "Op?"
}

func canonLines(lines ...string) string {
	cp := append([]string(nil), lines...)
	sort.Strings(cp)
	return strings.Join(cp, "\n") + "\n"
}

func btoa(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func sortedKeysType(m map[string]*ir.TypeDecl) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedKeysConst(m map[string]*ir.Const) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedKeysFunc(m map[string]*ir.Func) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
