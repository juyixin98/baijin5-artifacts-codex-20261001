package imapwire

import (
	"bytes"
	"errors"
	"io"
	"net"
	"strings"
	"testing"
)

func TestSeqSetResolve(t *testing.T) {
	cases := []struct {
		name  string
		input string
		max   int
		want  []int
	}{
		{"single", "3", 5, []int{3}},
		{"range", "1:5", 5, []int{1, 2, 3, 4, 5}},
		{"range clipped", "3:9", 5, []int{3, 4, 5}},
		{"list dedup", "2:4,3,4", 5, []int{2, 3, 4}},
		{"star", "*", 5, []int{5}},
		{"star empty mailbox", "*", 0, nil},
		{"reversed matches nothing", "5:1", 5, nil},
		{"out of range", "9:12", 5, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			set, err := ParseSeqSet(Token{Kind: TokAtom, Raw: []byte(tc.input)})
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			got, err := set.ResolveSeq(tc.max)
			if err != nil {
				t.Fatalf("resolve: %v", err)
			}
			if !intsEqual(got, tc.want) {
				t.Fatalf("%s: got %v want %v", tc.name, got, tc.want)
			}
		})
	}
}

func TestSeqSetRejectsBad(t *testing.T) {
	for _, bad := range []string{"0", "-3", "1:", ":5", "a", "1,,"} {
		_, err := ParseSeqSet(Token{Kind: TokAtom, Raw: []byte(bad)})
		if err == nil {
			t.Fatalf("%q should be rejected", bad)
		}
		var we *Error
		if !errors.As(err, &we) || we.Class != ClassInput {
			t.Fatalf("%q: want ClassInput, got %v", bad, err)
		}
	}
}

func TestSeqSetStarSemantics(t *testing.T) {
	set, _ := ParseSeqSet(Token{Kind: TokAtom, Raw: []byte("*")})
	if !set.IsUIDStar() {
		t.Fatal("* should be detected")
	}
	uid, err := set.ResolveUID(42)
	if err != nil || len(uid) != 1 || uid[0] != 42 {
		t.Fatalf("UID * want [42], got %v err=%v", uid, err)
	}
}

// TestFramingBinaryLiteral drives a full literal handshake over net.Pipe and
// asserts the recovered payload is byte-identical, including NUL and 0xFF;
// the length is counted in bytes.
func TestFramingBinaryLiteral(t *testing.T) {
	payload := bytes.Repeat([]byte{0x00, 0x01, 0xFF, 0x7F, '\r', '\n', 'A'}, 100)
	srvConn, cliConn := net.Pipe()
	defer srvConn.Close()
	defer cliConn.Close()

	// Continuation is written back down the same pipe the framer reads from.
	conn := NewConn(srvConn)
	fr := NewFramer(srvConn, conn)

	go func() {
		_, _ = cliConn.Write([]byte("A1 LOGIN {" + itoa(len(payload)) + "}\r\n"))
		buf := make([]byte, 64)
		n, _ := cliConn.Read(buf) // read "+ go ahead"
		if !strings.HasPrefix(string(buf[:n]), "+ go ahead") {
			t.Errorf("want continuation, got %q", buf[:n])
		}
		_, _ = cliConn.Write(payload)
		_, _ = cliConn.Write([]byte("\r\n"))
	}()

	cmd, err := fr.Frame()
	if err != nil {
		t.Fatalf("frame: %v", err)
	}
	if cmd.Tag != "A1" || cmd.Verb != "LOGIN" {
		t.Fatalf("bad preamble: %+v", cmd)
	}
	if len(cmd.Args) != 1 || cmd.Args[0].Kind != TokLiteral {
		t.Fatalf("want one literal arg, got %+v", cmd.Args)
	}
	if !bytes.Equal(cmd.Args[0].Raw, payload) {
		t.Fatalf("literal payload mismatch: len got=%d want=%d",
			len(cmd.Args[0].Raw), len(payload))
	}
}

func TestFramingParenthesizedList(t *testing.T) {
	pairs := []struct {
		in   string
		verb string
		n    int
	}{
		{"F1 FETCH 1:3 (UID FLAGS)\r\n", "FETCH", 2},
		{"S1 STORE 2 +FLAGS (\\Deleted \\Seen)\r\n", "STORE", 2},
	}
	for _, p := range pairs {
		fr := NewFramer(strings.NewReader(p.in), NewConn(io.Discard))
		cmd, err := fr.Frame()
		if err != nil {
			t.Fatalf("frame %q: %v", p.in, err)
		}
		if cmd.Verb != p.verb {
			t.Fatalf("verb got %s want %s", cmd.Verb, p.verb)
		}
		last := cmd.Args[len(cmd.Args)-1]
		if last.Kind != TokList || len(last.Item) != p.n {
			t.Fatalf("%s: want list of %d, got %+v", p.in, p.n, last)
		}
	}
}

func TestFramingBracketStaysOnAtom(t *testing.T) {
	in := "F1 UID FETCH 5 (BODY[HEADER.FIELDS (SUBJECT DATE)])\r\n"
	fr := NewFramer(strings.NewReader(in), NewConn(io.Discard))
	cmd, err := fr.Frame()
	if err != nil {
		t.Fatalf("frame: %v", err)
	}
	list := cmd.Args[2]
	if list.Kind != TokList || len(list.Item) != 1 {
		t.Fatalf("want one-item list, got %+v", list)
	}
	name := string(list.Item[0].Raw)
	if !strings.EqualFold(name, "BODY[HEADER.FIELDS (SUBJECT DATE)]") {
		t.Fatalf("section atom mangled: %q", name)
	}
}

func TestLiteralTooLargeIsResourceError(t *testing.T) {
	in := "A1 LOGIN {999999999}\r\n"
	fr := NewFramer(strings.NewReader(in), NewConn(io.Discard))
	fr.MaxLiteral = 10
	_, err := fr.Frame()
	if err == nil {
		t.Fatal("want resource error")
	}
	var we *Error
	if !errors.As(err, &we) || we.Class != ClassResource || we.Code != "TOOBIG" {
		t.Fatalf("want resource/TOOBIG, got %v", err)
	}
}

func TestFramingRejectsMalformed(t *testing.T) {
	cases := []string{
		"A1 LOGIN \"unterminated\r\n",
		"A1 LOGIN \"bad\\x\"\r\n",
		"A1 LOGIN {abc}\r\n",
		"A1 FETCH 1:2 (UID FLAGS\r\n",
		"A1  FETCH\r\n", // double space / missing verb
	}
	for _, in := range cases {
		fr := NewFramer(strings.NewReader(in), NewConn(io.Discard))
		_, err := fr.Frame()
		if err == nil {
			t.Fatalf("input %q should be rejected", in)
		}
	}
}

func TestFramingRunCounter(t *testing.T) {
	in := "A1 NOOP\r\nA2 NOOP\r\n"
	fr := NewFramer(strings.NewReader(in), NewConn(io.Discard))
	c1, err := fr.Frame()
	if err != nil {
		t.Fatal(err)
	}
	c2, err := fr.Frame()
	if err != nil {
		t.Fatal(err)
	}
	if fr.RunNo() != 2 || c1.Tag != "A1" || c2.Tag != "A2" {
		t.Fatalf("run tracking wrong: %s %s run=%d", c1.Tag, c2.Tag, fr.RunNo())
	}
	if c1.Verb != "NOOP" {
		t.Fatalf("verb wrong: %s", c1.Verb)
	}
}

func TestFramingQuotedAndAtomArgs(t *testing.T) {
	fr := NewFramer(strings.NewReader("A1 LOGIN \"user name\" {0}\r\n\r\n"), NewConn(io.Discard))
	cmd, err := fr.Frame()
	if err != nil {
		t.Fatal(err)
	}
	if len(cmd.Args) != 2 {
		t.Fatalf("want 2 args, got %d", len(cmd.Args))
	}
	if cmd.Args[0].Kind != TokString || string(cmd.Args[0].Raw) != "user name" {
		t.Fatalf("quoted arg wrong: %+v", cmd.Args[0])
	}
	if cmd.Args[1].Kind != TokLiteral || len(cmd.Args[1].Raw) != 0 {
		t.Fatalf("empty literal wrong: %+v", cmd.Args[1])
	}
}

func intsEqual(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}
