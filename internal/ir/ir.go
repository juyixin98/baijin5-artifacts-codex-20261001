package ir

import "rlmod/internal/frontend"

// Primitive type names built into RL.
const (
	PrimInt = "int"
	PrimStr = "str"
)

// TypeRef is a fully qualified reference to a type. Module is empty for
// primitives, type parameters inside a generic body, or local references
// before linking.
type TypeRef struct {
	Module string
	Name   string
}

func (t TypeRef) String() string {
	if t.Module == "" {
		return t.Name
	}
	return t.Module + "." + t.Name
}

// Param is a function parameter.
type Param struct {
	Name string
	Type TypeRef
}

// Op enumerates the stack-machine opcodes of the executable IR.
type Op uint8

const (
	OPushInt Op = iota
	OPushStr
	OLoadLocal
	OStoreLocal
	OLoadConst
	OCall
	ONeg
	OBin
	OJump
	OJumpIfFalse
	OReturn
	OPop
)

// Instr is one IR instruction. Which fields are meaningful depends on Op:
//
//	OPushInt     -> Int
//	OPushStr     -> Str
//	OLoadLocal / OStoreLocal -> Index
//	OLoadConst   -> Module, Str (const name)
//	OCall        -> Module, Str (function base name), Index (arity),
//	               Instance (monomorphization key, "" for non-generic)
//	OBin         -> Str (operator)
//	OJump/OJumpIfFalse -> Jump (instruction index)
//	OReturn      -> Index == 1 when a value is returned
type Instr struct {
	Op       Op
	Int      int64
	Str      string
	Module   string
	Instance string
	Index    int
	Jump     int
}

// Func is a compiled, fully monomorphized function body.
type Func struct {
	Module     string
	OrigName   string // declared name, without instantiation suffix
	Key        string // OrigName or OrigName[a,b]
	Generic    bool
	Params     []Param
	HasResult  bool
	Result     TypeRef
	LocalTypes []TypeRef
	Instrs     []Instr
	Line       int
	// dependency bookkeeping (filled during lowering; used by fingerprint/semdiff)
	constDeps   []string
	callDeps    []string
	genericDeps []string
}

// ConstDeps returns sorted const references inlined into this function body.
func (f *Func) ConstDeps() []string   { return dedupSort(f.constDeps) }
func (f *Func) CallDeps() []string    { return dedupSort(f.callDeps) }
func (f *Func) GenericDeps() []string { return dedupSort(f.genericDeps) }

// Const is a folded compile-time constant. Its value is embedded into every
// use site, which is why const value changes are interface-level events.
type Const struct {
	Module    string
	Name      string
	Type      TypeRef
	Kind      frontend.LitKind
	IntVal    int64
	StrVal    string
	Sensitive bool
}

// TypeDecl is a user-declared named type (nominal, over a primitive base).
type TypeDecl struct {
	Module string
	Name   string
	Base   TypeRef
}

// Module is one compiled source unit.
type Module struct {
	Name    string
	Imports []string
	Types   map[string]*TypeDecl
	Consts  map[string]*Const
	Funcs   map[string]*Func // non-generic and instantiated functions
	// GenericSrc holds the checked generic source declarations keyed by name.
	GenericSrc map[string]*GenericSource
}

// GenericSource keeps the AST of a generic function together with its
// declared type parameters. Concrete bodies are produced on demand.
type GenericSource struct {
	Module     string
	Name       string
	TypeParams []string
	Decl       *frontend.FuncDecl
}

// Program is a linked set of modules ready for interpretation.
type Program struct {
	// SchemaVersion is the fingerprint/ABI schema; fingerprints never reuse
	// across distinct schema values.
	SchemaVersion string
	// SemVer is the compilation semantic version (major.minor.patch).
	SemVer string
	// Modules in declaration/load order.
	Order   []string
	Modules map[string]*Module
}

func dedupSort(in []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, x := range in {
		if !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	return out
}

// FuncKey indexes a (possibly monomorphized) function globally.
func FuncKey(module, instanceKey string) string { return module + "." + instanceKey }

// Exported reports whether a top-level symbol is part of the public interface
// (Go-style: capitalized names are exported).
func Exported(name string) bool {
	if name == "" {
		return false
	}
	c := name[0]
	return c >= 'A' && c <= 'Z'
}
