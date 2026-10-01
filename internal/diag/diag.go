// Package diag defines structured error categories shared by the frontend,
// IR checker, specializer and interpreters.
package diag

import "fmt"

// Category is a stable, test-assertable failure class.
type Category string

const (
	CatSyntax         Category = "SYNTAX"
	CatUnresolved     Category = "UNRESOLVED"
	CatArity          Category = "ARITY"
	CatImpureDecl     Category = "IMPURE_DECL"
	CatStaticUnknown  Category = "STATIC_UNKNOWN"
	CatDivisionByZero Category = "DIVISION_BY_ZERO"
	CatBudget         Category = "BUDGET_EXHAUSTED"
	CatStackOverflow  Category = "STACK_OVERFLOW"
	CatInput          Category = "INPUT_EXHAUSTED"
	CatConfig         Category = "CONFIG"
	CatInternal       Category = "INTERNAL"
)

// Error carries a machine readable category and an optional source position.
type Error struct {
	Category Category
	Message  string
	Line     int
	Col      int
	Cause    error
}

func New(cat Category, msg string) *Error {
	return &Error{Category: cat, Message: msg}
}

func Wrap(cat Category, msg string, cause error) *Error {
	return &Error{Category: cat, Message: msg, Cause: cause}
}

// At annotates the error with a source position when one is not present yet.
func (e *Error) At(line, col int) *Error {
	if e.Line == 0 {
		e.Line, e.Col = line, col
	}
	return e
}

func (e *Error) Error() string {
	pos := ""
	if e.Line > 0 {
		pos = fmt.Sprintf(" (line %d col %d)", e.Line, e.Col)
	}
	if e.Cause != nil {
		return fmt.Sprintf("%s%s: %s: %v", e.Category, pos, e.Message, e.Cause)
	}
	return fmt.Sprintf("%s%s: %s", e.Category, pos, e.Message)
}

func (e *Error) Unwrap() error { return e.Cause }

// Is reports whether err belongs to the given category.
func Is(err error, cat Category) bool {
	var de *Error
	if ok := As(err, &de); !ok {
		return false
	}
	return de.Category == cat
}

// As mirrors errors.As without importing it everywhere.
func As(err error, target **Error) bool {
	for err != nil {
		if e, ok := err.(*Error); ok {
			*target = e
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

// CategoryOf extracts the category; unknown errors map to CatInternal.
func CategoryOf(err error) Category {
	var de *Error
	if As(err, &de) {
		return de.Category
	}
	if err == nil {
		return ""
	}
	return CatInternal
}
