// Package diag defines fixed diagnostic codes shared by the frontend,
// linker and interpreter. Codes are part of the documented boundary
// semantics: tests assert on them rather than on human-readable strings.
package diag

// Error codes returned by the linker pipeline.
const (
	// EBadObject: an object file is malformed (truncated, bad magic, bad index).
	EBadObject = "E_BAD_OBJECT"
	// EBadAsm: an assembly source line cannot be parsed/assembled.
	EBadAsm = "E_BAD_ASM"
	// EBadConfig: a link configuration refers to a missing symbol/section.
	EBadConfig = "E_BAD_CONFIG"
	// EMultipleStrong: two or more strong definitions of the same symbol.
	EMultipleStrong = "E_MULTIPLE_STRONG"
	// EUndefinedStrong: a strong relocation targets an undefined symbol.
	EUndefinedStrong = "E_UNDEFINED_STRONG"
	// ENoEntry: the configured entry symbol is missing or not placed.
	ENoEntry = "E_NO_ENTRY"
	// EDanglingReloc: a kept section holds a relocation against a
	// section that was discarded (explicit discard or GC).
	EDanglingReloc = "E_DANGLING_RELOC"
	// ERunFault: the interpreter executed an invalid/unmapped access.
	ERunFault = "E_RUN_FAULT"
)

// Warning codes. Warnings never turn a successful link into a failure.
const (
	// WWeakOverride: a strong definition shadows one or more weak ones.
	WWeakOverride = "W_WEAK_OVERRIDE"
	// WNullReloc: a weak-edge relocation resolved to a null/undefined weak symbol.
	WNullReloc = "W_NULL_RELOC"
	// WExternUndef: an undefined symbol is declared but never referenced.
	WExternUndef = "W_EXTERN_UNDEF"
)

// Diagnostic is one machine-readable diagnostic tied to an input identity.
type Diagnostic struct {
	Code     string `json:"code"`
	Object   string `json:"object,omitempty"`
	Section  string `json:"section,omitempty"`
	Symbol   string `json:"symbol,omitempty"`
	Message  string `json:"message"`
}

// Error makes Diagnostic usable as an error.
func (d Diagnostic) Error() string {
	where := d.Object
	if d.Section != "" {
		where += "/" + d.Section
	}
	if d.Symbol != "" {
		where += " symbol=" + d.Symbol
	}
	if where == "" {
		return d.Code + ": " + d.Message
	}
	return d.Code + " [" + where + "]: " + d.Message
}
