package difftest

import "genfsm/internal/semerr"

func kindOf(err error) string {
	if se, ok := semerr.As(err); ok {
		return string(se.Kind)
	}
	return "OTHER"
}

func codeOf(err error) string {
	if se, ok := semerr.As(err); ok {
		return string(se.Code)
	}
	return ""
}
