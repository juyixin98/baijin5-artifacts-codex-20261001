package runtime

import "fmt"

// Value is the runtime tagged value shared by both interpreters.
type Value struct {
	Kind ValueKind
	I    int64
	S    string
	B    bool
}

type ValueKind int

const (
	VNil ValueKind = iota
	VInt
	VStr
	VBool
)

func IntVal(i int64) Value  { return Value{Kind: VInt, I: i} }
func StrVal(s string) Value { return Value{Kind: VStr, S: s} }
func BoolVal(b bool) Value  { return Value{Kind: VBool, B: b} }
func NilVal() Value         { return Value{Kind: VNil} }

func (v Value) Truthy() bool {
	switch v.Kind {
	case VNil:
		return false
	case VBool:
		return v.B
	case VInt:
		return v.I != 0
	case VStr:
		return v.S != ""
	default:
		return false
	}
}

func (v Value) Display() string {
	switch v.Kind {
	case VNil:
		return "nil"
	case VInt:
		return fmt.Sprintf("%d", v.I)
	case VBool:
		if v.B {
			return "true"
		}
		return "false"
	case VStr:
		return v.S
	}
	return "<?>"
}

func (v Value) Equals(o Value) bool {
	if v.Kind != o.Kind {
		return false
	}
	switch v.Kind {
	case VNil:
		return true
	case VInt:
		return v.I == o.I
	case VStr:
		return v.S == o.S
	case VBool:
		return v.B == o.B
	}
	return false
}
