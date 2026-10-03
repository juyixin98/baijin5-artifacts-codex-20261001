// Package semdiff compares the link/run pipeline against hand-authored
// expected-answer tables. Expected answers are authored independently of
// the linker implementation; the pipeline never generates its own oracle.
package semdiff

import (
	"bufio"
	"fmt"
	"io"
	"strconv"
	"strings"
)

// SpecVersion is the supported expectation-table version.
const SpecVersion = 1

// Expectation is one independently authored test case.
type Expectation struct {
	ID      string
	Objects []string // .mkobj files (assembled fixtures), in link order
	Entry   string

	WantErrorKind string // empty means link must succeed
	WantReturn    int32
	WantReturnSet bool
	WantKept      []string // "object.section"
	WantDropped   []string
	WantUndefined []string // symbol names expected in diagnostics
}

// Spec is a parsed expectation file.
type Spec struct {
	Version int
	Cases   []*Expectation
}

// ParseSpec parses the table format:
//
//	spec = 1
//	case <id>
//	objects = a.o b.o
//	entry = main
//	expect = ok | error:<kind>
//	return = 42
//	kept = a.o:.text.main b.o:.text.helper
//	dropped = c.o:.text.dead
//	undefined = ghost
func ParseSpec(r io.Reader) (*Spec, error) {
	sc := bufio.NewScanner(r)
	spec := &Spec{}
	var cur *Expectation
	ln := 0
	flush := func() error {
		if cur != nil {
			if err := cur.validate(); err != nil {
				return err
			}
			spec.Cases = append(spec.Cases, cur)
		}
		return nil
	}
	for sc.Scan() {
		ln++
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, val, ok := strings.Cut(line, "=")
		if !ok {
			// tolerate "case <id>" style
			if f := strings.Fields(line); len(f) == 2 && f[0] == "case" {
				if err := flush(); err != nil {
					return nil, err
				}
				cur = &Expectation{ID: f[1]}
				continue
			}
			return nil, fmt.Errorf("semdiff line %d: malformed %q", ln, line)
		}
		key = strings.TrimSpace(key)
		val = strings.TrimSpace(val)
		if key == "spec" {
			v, err := strconv.Atoi(val)
			if err != nil {
				return nil, fmt.Errorf("semdiff line %d: bad spec version", ln)
			}
			spec.Version = v
			continue
		}
		if cur == nil {
			return nil, fmt.Errorf("semdiff line %d: key before any 'case'", ln)
		}
		switch key {
		case "objects":
			cur.Objects = strings.Fields(val)
		case "entry":
			cur.Entry = val
		case "expect":
			if val == "ok" {
				cur.WantErrorKind = ""
			} else if kind, ok := strings.CutPrefix(val, "error:"); ok {
				cur.WantErrorKind = strings.TrimSpace(kind)
			} else {
				return nil, fmt.Errorf("semdiff line %d: bad expect %q", ln, val)
			}
		case "return":
			n, err := strconv.Atoi(val)
			if err != nil {
				return nil, fmt.Errorf("semdiff line %d: bad return", ln)
			}
			cur.WantReturn = int32(n)
			cur.WantReturnSet = true
		case "kept":
			cur.WantKept = strings.Fields(val)
		case "dropped":
			cur.WantDropped = strings.Fields(val)
		case "undefined":
			cur.WantUndefined = strings.Fields(val)
		default:
			return nil, fmt.Errorf("semdiff line %d: unknown key %q", ln, key)
		}
	}
	if spec.Version != SpecVersion {
		return nil, fmt.Errorf("semdiff: unsupported spec version %d (want %d)", spec.Version, SpecVersion)
	}
	if err := flush(); err != nil {
		return nil, err
	}
	return spec, sc.Err()
}

func (e *Expectation) validate() error {
	if e.ID == "" {
		return fmt.Errorf("semdiff: case without id")
	}
	if len(e.Objects) == 0 {
		return fmt.Errorf("semdiff case %s: no objects", e.ID)
	}
	if e.WantErrorKind != "" && e.WantReturnSet {
		return fmt.Errorf("semdiff case %s: cannot expect both error and return", e.ID)
	}
	return nil
}
