package imapwire

import (
	"sort"
	"strconv"
)

// SeqRange is one inclusive range of a sequence set; To==0 with Star means
// "*" (highest value in the mailbox), resolved later against the mailbox max.
type SeqRange struct {
	From  int
	To    int
	StarF bool // From is "*"
	StarT bool // To is "*"
}

// SeqSet is a parsed sequence set.
type SeqSet struct {
	Ranges []SeqRange
}

// MaxSetRanges bounds the number of explicit ranges per command.
const MaxSetRanges = 100_000

// IsUIDStar reports whether the set is exactly "*".
func (s SeqSet) IsUIDStar() bool {
	return len(s.Ranges) == 1 && s.Ranges[0].StarF && s.Ranges[0].StarT
}

// ParseSeqSet parses one token as a sequence-set, where "*" denotes the
// highest message number in the mailbox.
func ParseSeqSet(tok Token) (SeqSet, error) {
	if tok.Kind != TokAtom {
		return SeqSet{}, badf("sequence set must be unquoted atom, got %s", kindName(tok.Kind))
	}
	s := string(tok.Raw)
	var set SeqSet
	for _, part := range splitComma(s) {
		if part == "" {
			return SeqSet{}, badf("empty range in sequence set")
		}
		lo, hi := part, part
		if i := indexColon(part); i >= 0 {
			lo, hi = part[:i], part[i+1:]
		}
		from, fromStar, err := parseSeqNum(lo)
		if err != nil {
			return SeqSet{}, err
		}
		rg := SeqRange{From: from, StarF: fromStar}
		if i := indexColon(part); i >= 0 {
			to, toStar, err := parseSeqNum(hi)
			if err != nil {
				return SeqSet{}, err
			}
			rg.To, rg.StarT = to, toStar
		} else {
			rg.To, rg.StarT = from, fromStar
		}
		set.Ranges = append(set.Ranges, rg)
		if len(set.Ranges) > MaxSetRanges {
			return SeqSet{}, NewCodedError(ClassResource, "TOOMANY",
				"sequence set exceeds %d ranges", MaxSetRanges)
		}
	}
	if len(set.Ranges) == 0 {
		return SeqSet{}, badf("empty sequence set")
	}
	return set, nil
}

func parseSeqNum(s string) (int, bool, error) {
	if s == "*" {
		return 0, true, nil
	}
	n, err := strconv.Atoi(s)
	if err != nil || n <= 0 {
		return 0, false, badf("invalid sequence number %q (must be >= 1)", s)
	}
	return n, false, nil
}

func splitComma(s string) []string {
	var out []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == ',' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	return append(out, s[start:])
}

func indexColon(s string) int {
	for i := 0; i < len(s); i++ {
		if s[i] == ':' {
			return i
		}
	}
	return -1
}

// ResolveUID expands the set against uidMax (the mailbox's highest UID) and
// returns de-duplicated, ascending UIDs. "*" against an empty mailbox
// (uidMax==0) matches nothing.
func (s SeqSet) ResolveUID(uidMax uint32) ([]uint32, error) {
	return s.resolve(uidMax)
}

// ResolveSeq expands the set against count (number of existing messages) into
// 1-based sequence positions, de-duplicated and ascending.
func (s SeqSet) ResolveSeq(count int) ([]int, error) {
	nums, err := s.resolve(uint32(count))
	if err != nil {
		return nil, err
	}
	out := make([]int, len(nums))
	for i, n := range nums {
		out[i] = int(n)
	}
	return out, nil
}

func (s SeqSet) resolve(max uint32) ([]uint32, error) {
	seen := make(map[uint32]struct{})
	total := 0
	for _, rg := range s.Ranges {
		lo, hi := uint32(rg.From), uint32(rg.To)
		if rg.StarF {
			lo = max
		}
		if rg.StarT {
			hi = max
		}
		if lo == 0 || hi == 0 {
			continue // "*" in an empty mailbox
		}
		if lo > hi {
			continue // reversed range matches nothing (RFC 9051)
		}
		if lo > max {
			continue
		}
		if hi > max {
			hi = max
		}
		total += int(hi-lo) + 1
		if total > MaxExpansion {
			return nil, NewCodedError(ClassResource, "TOOMANY",
				"sequence set expands to more than %d messages", MaxExpansion)
		}
		for v := lo; v <= hi; v++ {
			seen[v] = struct{}{}
			if len(seen) > MaxExpansion {
				return nil, NewCodedError(ClassResource, "TOOMANY",
					"sequence set expands to more than %d distinct messages", MaxExpansion)
			}
		}
	}
	out := make([]uint32, 0, len(seen))
	for v := range seen {
		out = append(out, v)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out, nil
}

// MaxExpansion bounds one command's fan-out so "1:4294967295" cannot exhaust
// memory. A deliberate resource guard surfaced as resource-exhausted.
const MaxExpansion = 1_000_000

func kindName(k TokenKind) string {
	switch k {
	case TokAtom:
		return "atom"
	case TokString:
		return "quoted"
	case TokLiteral:
		return "literal"
	case TokList:
		return "list"
	}
	return "unknown"
}
