package wire

import (
	"fmt"
	"strconv"
	"strings"

	"imapd/internal/errs"
)

// MaxSeqSetExpansion caps how many numbers a sequence set may expand to.
// It is the resource guard against sets like "1:4294967295".
const MaxSeqSetExpansion = 100000

// ParseSeqSet expands an IMAP sequence set ("1:3,5,*") against max, the
// current value of "*" (message count for sequence addressing, largest
// UID for UID addressing). Numbers are returned in protocol order
// (descending ranges stay descending); callers intersect with existing
// messages. A bare number larger than max is kept as-is: range checking
// is the caller's policy (state error for sequence FETCH, no-match for
// UID FETCH).
func ParseSeqSet(s string, max uint32) ([]uint32, error) {
	if s == "" {
		return nil, errs.New(errs.CatInput, "wire.seqset", "empty sequence set")
	}
	var out []uint32
	for _, part := range strings.Split(s, ",") {
		lo, hi, err := parseRange(part, max)
		if err != nil {
			return nil, err
		}
		span := rangeSpan(lo, hi)
		if uint64(len(out))+span > MaxSeqSetExpansion {
			return nil, errs.New(errs.CatResource, "wire.seqset",
				fmt.Sprintf("sequence set expands beyond %d entries", MaxSeqSetExpansion))
		}
		if lo <= hi {
			for n := lo; ; n++ {
				out = append(out, n)
				if n == hi {
					break
				}
			}
		} else {
			for n := lo; ; n-- {
				out = append(out, n)
				if n == hi {
					break
				}
			}
		}
	}
	return out, nil
}

func rangeSpan(lo, hi uint32) uint64 {
	if lo <= hi {
		return uint64(hi) - uint64(lo) + 1
	}
	return uint64(lo) - uint64(hi) + 1
}

func parseRange(part string, max uint32) (lo, hi uint32, err error) {
	if part == "" {
		return 0, 0, errs.New(errs.CatInput, "wire.seqset", "empty range in sequence set")
	}
	if i := strings.IndexByte(part, ':'); i >= 0 {
		lo, err = parseSeqNumber(part[:i], max)
		if err != nil {
			return 0, 0, err
		}
		hi, err = parseSeqNumber(part[i+1:], max)
		if err != nil {
			return 0, 0, err
		}
		return lo, hi, nil
	}
	lo, err = parseSeqNumber(part, max)
	if err != nil {
		return 0, 0, err
	}
	return lo, lo, nil
}

func parseSeqNumber(s string, max uint32) (uint32, error) {
	if s == "*" {
		return max, nil
	}
	n, err := strconv.ParseUint(s, 10, 32)
	if err != nil {
		return 0, errs.New(errs.CatInput, "wire.seqset",
			fmt.Sprintf("invalid sequence number %q", s))
	}
	if n == 0 {
		return 0, errs.New(errs.CatInput, "wire.seqset", "sequence numbers start at 1")
	}
	return uint32(n), nil
}
