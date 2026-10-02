package server

import (
	"imaplite/internal/imapwire"
)

// Thin constructors keep command code declarative about failure class.

func wireBad(format string, args ...any) error {
	return imapwire.NewError(imapwire.ClassInput, format, args...)
}
func wireUnknown(format string, args ...any) error {
	return imapwire.NewError(imapwire.ClassUnknownCommand, format, args...)
}
func wireUnsupported(format string, args ...any) error {
	return imapwire.NewError(imapwire.ClassUnsupportedItem, format, args...)
}
func wireState(format string, args ...any) error {
	return imapwire.NewError(imapwire.ClassState, format, args...)
}
func wireStateCode(code, format string, args ...any) error {
	return imapwire.NewCodedError(imapwire.ClassState, code, format, args...)
}
func wireAuth(format string, args ...any) error {
	return imapwire.NewCodedError(imapwire.ClassAuth, "AUTHENTICATIONFAILED", format, args...)
}
func wireAuthUnsupported(format string, args ...any) error {
	return imapwire.NewCodedError(imapwire.ClassAuthUnsupported, "UNSUPPORTED-STEPS", format, args...)
}
func wirePermission(format string, args ...any) error {
	return imapwire.NewCodedError(imapwire.ClassPermission, "NOPERM", format, args...)
}
func wireCompute(format string, args ...any) error {
	return imapwire.NewCodedError(imapwire.ClassCompute, "SERVERBUG", format, args...)
}

func toUpper(s string) string {
	b := []byte(s)
	for i := range b {
		if b[i] >= 'a' && b[i] <= 'z' {
			b[i] -= 'a' - 'A'
		}
	}
	return string(b)
}

func quoteToken(t imapwire.Token) []byte {
	switch t.Kind {
	case imapwire.TokAtom, imapwire.TokString, imapwire.TokLiteral:
		return t.Raw
	}
	return nil
}
