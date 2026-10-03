package wire

import (
	"bytes"
	"io"
	"reflect"
	"strings"
	"testing"

	"imapd/internal/errs"
	"imapd/internal/testlog"
)

func readCmd(t *testing.T, input string) (*Command, error) {
	t.Helper()
	rd := NewReader(strings.NewReader(input))
	return rd.ReadCommand(nil)
}

func TestReadCommandSimple(t *testing.T) {
	cmd, err := readCmd(t, "a1 SELECT INBOX\r\n")
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	if cmd.Tag != "a1" || cmd.Name != "SELECT" || string(cmd.Args) != "INBOX" {
		t.Fatalf("got %+v", cmd)
	}
}

func TestReadCommandVerbCaseNormalised(t *testing.T) {
	cmd, err := readCmd(t, "x select INBOX\r\n")
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	if cmd.Name != "SELECT" {
		t.Fatalf("name = %q, want SELECT", cmd.Name)
	}
}

func TestReadCommandLiteralCountsBytes(t *testing.T) {
	lg := testlog.New(t)
	// 报表 is 6 bytes in UTF-8; declaring {2} (rune count) must desync —
	// the declared length is bytes, so {6} is the correct framing.
	cmd, err := readCmd(t, "b1 SELECT {6}\r\n报表\r\n")
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	lg.Log("literal-utf8", "literal length is bytes, not runes",
		"args_len", len(cmd.Args), "args", string(cmd.Args))
	if string(cmd.Args) != "报表" || len(cmd.Args) != 6 {
		t.Fatalf("args = %q (%d bytes), want 报表 (6 bytes)", cmd.Args, len(cmd.Args))
	}
}

func TestReadCommandBinaryLiteral(t *testing.T) {
	lg := testlog.New(t)
	// Payload contains NUL, CR, LF and ends with "{3}" — the framing must
	// not mistake payload bytes for a new literal marker.
	payload := []byte{'A', 0x00, '\r', '\n', 0xff, '{', '3', '}'}
	var in bytes.Buffer
	in.WriteString("c1 SELECT {8}\r\n")
	in.Write(payload)
	in.WriteString("\r\n")
	cmd, err := NewReader(&in).ReadCommand(nil)
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	lg.Log("literal-binary", "binary payload must survive framing byte-exactly",
		"payload_len", len(cmd.Args))
	if !bytes.Equal(cmd.Args, payload) {
		t.Fatalf("args = %v, want %v", cmd.Args, payload)
	}
}

func TestReadCommandLiteralTooLargeIsResourceError(t *testing.T) {
	lg := testlog.New(t)
	rd := NewReader(strings.NewReader("r1 SELECT {2097153}\r\n"))
	_, err := rd.ReadCommand(nil)
	lg.Log("literal-huge", "oversized literal must be a resource error",
		"category", errs.CategoryOf(err).String())
	if errs.CategoryOf(err) != errs.CatResource {
		t.Fatalf("category = %v, want resource", errs.CategoryOf(err))
	}
}

func TestReadCommandLineTooLongIsResourceError(t *testing.T) {
	lg := testlog.New(t)
	rd := NewReader(strings.NewReader(strings.Repeat("A", 9000) + "\r\n"))
	_, err := rd.ReadCommand(nil)
	lg.Log("line-huge", "oversized line must be a resource error",
		"category", errs.CategoryOf(err).String())
	if errs.CategoryOf(err) != errs.CatResource {
		t.Fatalf("category = %v, want resource", errs.CategoryOf(err))
	}
}

func TestReadCommandMalformedInputs(t *testing.T) {
	lg := testlog.New(t)
	cases := map[string]string{
		"missing tag":       " SELECT INBOX\r\n",
		"missing verb":      "a1\r\n",
		"bad tag char":      "a* SELECT INBOX\r\n",
		"non-sync literal":  "a1 SELECT {5+}\r\nINBOX\r\n",
		"non-numeric lit":   "a1 SELECT {abc}\r\n",
		"empty literal len": "a1 SELECT {}\r\n",
	}
	for name, input := range cases {
		_, err := readCmd(t, input)
		lg.Log("malformed", "malformed input must be an input error",
			"case", name, "category", errs.CategoryOf(err).String())
		if errs.CategoryOf(err) != errs.CatInput {
			t.Fatalf("%s: category = %v, want input (err=%v)", name, errs.CategoryOf(err), err)
		}
	}
}

func TestReadCommandEOF(t *testing.T) {
	rd := NewReader(strings.NewReader(""))
	if _, err := rd.ReadCommand(nil); err != io.EOF {
		t.Fatalf("err = %v, want io.EOF", err)
	}
}

func TestParseSeqSet(t *testing.T) {
	lg := testlog.New(t)
	cases := []struct {
		in   string
		max  uint32
		want []uint32
	}{
		{"1", 5, []uint32{1}},
		{"1:3,5", 5, []uint32{1, 2, 3, 5}},
		{"3:1", 5, []uint32{3, 2, 1}}, // descending ranges stay descending
		{"*", 4, []uint32{4}},
		{"1:*", 3, []uint32{1, 2, 3}},
		{"2,2,3", 5, []uint32{2, 2, 3}}, // duplicates preserved; caller dedups
	}
	for _, c := range cases {
		got, err := ParseSeqSet(c.in, c.max)
		if err != nil {
			t.Fatalf("%s: err = %v", c.in, err)
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Fatalf("%s (max %d) = %v, want %v", c.in, c.max, got, c.want)
		}
	}
	lg.Log("seqset", "expansion must preserve protocol order and resolve * against max",
		"cases", len(cases))
}

func TestParseSeqSetInvalid(t *testing.T) {
	lg := testlog.New(t)
	for _, in := range []string{"", "0", "x", "1:", ":3", "1,,2", "4294967296"} {
		_, err := ParseSeqSet(in, 5)
		lg.Log("seqset-invalid", "invalid set must be an input error",
			"input", in, "category", errs.CategoryOf(err).String())
		if errs.CategoryOf(err) != errs.CatInput {
			t.Fatalf("%q: category = %v, want input", in, errs.CategoryOf(err))
		}
	}
}

func TestParseSeqSetExplosionIsResourceError(t *testing.T) {
	lg := testlog.New(t)
	_, err := ParseSeqSet("1:4294967295", 4294967295)
	lg.Log("seqset-explosion", "giant expansion must be a resource error",
		"category", errs.CategoryOf(err).String())
	if errs.CategoryOf(err) != errs.CatResource {
		t.Fatalf("category = %v, want resource", errs.CategoryOf(err))
	}
}

func TestWriterUntaggedFetchFraming(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)
	err := w.UntaggedFetch(3, []Part{
		{Text: "UID 9"},
		{Text: "BODY[]", Literal: []byte{'x', 0x00, '\r', '\n'}},
	})
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	want := "* 3 FETCH (UID 9 BODY[] {4}\r\nx\x00\r\n)\r\n"
	if buf.String() != want {
		t.Fatalf("got %q, want %q", buf.String(), want)
	}
}
