package store

import "strings"

// Flags are stored as a canonical space-joined string, always sorted and
// de-duplicated, so "same flags" is byte-equal and hasFlag is a simple check.
// Input flag names are atom tokens such as "\\Seen" from the parser.

// encodeFlags canonicalises flags for storage.
func encodeFlags(flags []string) string {
	seen := make(map[string]struct{}, len(flags))
	uniq := make([]string, 0, len(flags))
	for _, f := range flags {
		f = strings.TrimSpace(f)
		if f == "" {
			continue
		}
		key := strings.ToLower(f)
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		uniq = append(uniq, f)
	}
	// Case-insensitive ordering keeps the stored form deterministic.
	sortStringsFold(uniq)
	return strings.Join(uniq, " ")
}

// decodeFlags parses the stored canonical form back into a slice.
func decodeFlags(s string) []string {
	if s == "" {
		return nil
	}
	parts := strings.Split(s, " ")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// hasFlag checks membership case-insensitively against the canonical string.
func hasFlag(stored, flag string) bool {
	for _, f := range strings.Fields(stored) {
		if strings.EqualFold(f, flag) {
			return true
		}
	}
	return false
}

// mergeFlags applies mode to old ∪/− flags without mutating old.
func mergeFlags(old, flags []string, mode FlagMode) []string {
	set := make(map[string]string, len(old))
	for _, f := range old {
		set[strings.ToLower(f)] = f
	}
	switch mode {
	case FlagReplace:
		clear(set)
		for _, f := range flags {
			set[strings.ToLower(f)] = f
		}
	case FlagAdd:
		for _, f := range flags {
			set[strings.ToLower(f)] = f
		}
	case FlagRemove:
		for _, f := range flags {
			delete(set, strings.ToLower(f))
		}
	}
	out := make([]string, 0, len(set))
	for _, f := range set {
		out = append(out, f)
	}
	sortStringsFold(out)
	return out
}

func sortStringsFold(s []string) {
	// Tiny insertion sort: flag lists are at most a handful of entries.
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && strings.ToLower(s[j-1]) > strings.ToLower(s[j]); j-- {
			s[j-1], s[j] = s[j], s[j-1]
		}
	}
}
