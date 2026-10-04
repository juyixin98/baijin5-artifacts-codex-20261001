package objfmt

// Magic and Version identify the custom relocatable object format "MOBJ".
// Layout is little-endian and fully specified in objfmt/codec.go.
const (
	Magic   = "MOBJ"
	Version = uint16(1)
)

// Binding of a symbol.
const (
	BindLocal = uint8(0) // local definition, not visible across objects
	BindWeak  = uint8(1) // weak definition / weak undefined reference
	BindGlobal = uint8(2) // strong global definition / strong extern
)

// Section flags.
const (
	// FlagAlloc: section is allocated into the linked image and takes
	// part in reachability-based section reclaim.
	FlagAlloc = uint8(1 << 0)
	// FlagReadOnly: placement hint (has no GC effect).
	FlagReadOnly = uint8(1 << 1)
)

// Relocation types.
const (
	// RelAbs32: patch 4 absolute bytes with the target symbol value.
	RelAbs32 = uint8(1)
	// RelPCRel8: patch 1 signed relative byte: target - (reloc offset + 1).
	RelPCRel8 = uint8(2)
)

// Section is one named byte blob.
type Section struct {
	Name  string
	Flags uint8
	Data  []byte
}

// Symbol is one symbol table entry.
type Symbol struct {
	Name    string
	Section uint16 // 0xFFFF means undefined (NSEC)
	Value   uint32
	Size    uint32
	Binding uint8
	Export  bool // exported symbols are GC roots when ExportRoots is on
}

// UndefinedSection is the sentinel section index for undefined symbols.
const UndefinedSection = uint16(0xFFFF)

// Reloc is a relocation attached to a section.
type Reloc struct {
	Offset uint32
	Type   uint8
	// EdgeWeak marks a "weak reference": the relocation does not itself
	// keep its target section alive in reachability analysis.
	EdgeWeak bool
	Symbol   uint16
	Addend   int32
}

// Object is one relocatable object file.
type Object struct {
	Name     string // assigned by the reader; not serialized
	Sections []Section
	Symbols  []Symbol
	Relocs   map[uint16][]Reloc // keyed by section index
}
