package semdiff

import (
	"strings"
	"testing"
)

func TestParseSpec(t *testing.T) {
	src := `spec = 1
case c1
objects = a.mkobj
entry = main
expect = ok
return = 3
kept = a.o:.t
`
	spec, err := ParseSpec(strings.NewReader(src))
	if err != nil {
		t.Fatal(err)
	}
	if len(spec.Cases) != 1 {
		t.Fatalf("cases=%d", len(spec.Cases))
	}
	c := spec.Cases[0]
	if c.ID != "c1" || c.Entry != "main" || !c.WantReturnSet || c.WantReturn != 3 ||
		len(c.WantKept) != 1 || c.WantErrorKind != "" {
		t.Fatalf("case mismatch: %+v", c)
	}
}

func TestParseSpecErrors(t *testing.T) {
	cases := map[string]string{
		"bad version":      "spec = 9\ncase x\nobjects = a.mkobj\n",
		"key before case":  "objects = a.mkobj\n",
		"no objects":       "spec = 1\ncase x\nentry = main\n",
		"error and return": "spec = 1\ncase x\nobjects = a.mkobj\nexpect = error:runtime\nreturn = 1\n",
		"unknown key":      "spec = 1\ncase x\nobjects = a.mkobj\nbogus = 1\n",
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseSpec(strings.NewReader(body)); err == nil {
				t.Fatalf("expected error: %s", name)
			}
		})
	}
}
