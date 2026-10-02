// Package errcat defines the closed set of failure categories used across the
// frontend, partial evaluator and interpreter. Tests assert on these codes
// instead of comparing human readable error strings.
package errcat

import "fmt"

// Category is a stable, testable failure classification.
type Category string

const (
	None             Category = ""
	Syntax           Category = "E_SYNTAX"
	DuplicateDecl    Category = "E_DUPDECL"
	UndefinedSymbol  Category = "E_UNDEFINED"
	Arity            Category = "E_ARITY"
	Type             Category = "E_TYPE"
	PurityViolation  Category = "E_PURITY"
	BudgetExceeded   Category = "E_BUDGET"
	DivisionByZero   Category = "E_DIVZERO"
	StackDepth       Category = "E_STACKDEPTH"
	NoReturn         Category = "E_NORETURN"
	Unknown          Category = "E_UNKNOWN"
)

// Error is an error carrying a stable Category.
type Error struct {
	Cat Category
	Msg string
}

func (e *Error) Error() string {
	if e.Cat == None {
		return e.Msg
	}
	return string(e.Cat) + ": " + e.Msg
}

// New builds an error with the given category.
func New(cat Category, format string, args ...any) error {
	return &Error{Cat: cat, Msg: fmt.Sprintf(format, args...)}
}

// Of returns the Category of err, or Unknown for non classified errors.
func Of(err error) Category {
	if err == nil {
		return None
	}
	var ce *Error
	if As(err, &ce) {
		return ce.Cat
	}
	return Unknown
}

// As is a small indirection so callers can avoid importing errors directly.
func As(err error, target **Error) bool {
	for err != nil {
		if e, ok := err.(*Error); ok {
			*target = e
			return true
		}
		type unwrapper interface{ Unwrap() error }
		u, ok := err.(unwrapper)
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}
