package imapwire

import (
	"bytes"
	"strings"
	"testing"
)

func TestConnEncodesRecords(t *testing.T) {
	var buf bytes.Buffer
	c := NewConn(&buf)

	c.WriteCapability([]string{"IMAP4rev1", "UIDPLUS"})
	c.WriteExists(5)
	c.WriteRecent(0)
	c.WriteExpunge(2)
	c.WriteFlags([]string{`\Seen`, `\Deleted`})
	c.WriteUntaggedOK("UIDVALIDITY 1001", "")
	c.WriteTagged("t1", StatusOK, "READ-WRITE", "INBOX selected")
	c.WriteContinue("go ahead")
	c.WriteBye("bye")

	out := buf.String()
	wants := []string{
		"* CAPABILITY (IMAP4rev1 UIDPLUS)\r\n",
		"* 5 EXISTS\r\n",
		"* 0 RECENT\r\n",
		"* 2 EXPUNGE\r\n",
		"* FLAGS (\\Seen \\Deleted)\r\n",
		"* OK [UIDVALIDITY 1001]\r\n",
		"t1 OK [READ-WRITE] INBOX selected\r\n",
		"+ go ahead\r\n",
		"* BYE bye\r\n",
	}
	for _, w := range wants {
		if !strings.Contains(out, w) {
			t.Fatalf("output missing %q\nfull:\n%s", w, out)
		}
	}
}

func TestWriteFetchAtomicallyAndLiterally(t *testing.T) {
	var buf bytes.Buffer
	c := NewConn(&buf)
	c.WriteFetch(3, []FetchItem{
		{Name: "UID", Kind: KindNumber, Num: 7},
		{Name: "FLAGS", Kind: KindFlagList, Flags: []string{`\Seen`}},
		{Name: "INTERNALDATE", Kind: KindQuoted, Text: "01-Sep-2025 08:00:00 +0000"},
		{Name: "BODY[]", Kind: KindLiteral, Raw: []byte("a\rb\n\xff")},
	})
	want := "* 3 FETCH (UID 7 FLAGS (\\Seen) INTERNALDATE " +
		`"01-Sep-2025 08:00:00 +0000" BODY[] {5}` + "\r\n" +
		"a\rb\n\xff)\r\n"
	if got := buf.String(); got != want {
		t.Fatalf("fetch encoding mismatch\n got: %q\nwant: %q", got, want)
	}
}

func TestEncodeStringChoosesQuotedOrLiteral(t *testing.T) {
	if got := string(EncodeString(nil)); got != "NIL" {
		t.Fatalf("nil want NIL, got %q", got)
	}
	if got := string(EncodeString([]byte(`plain`))); got != `"plain"` {
		t.Fatalf("safe want quoted, got %q", got)
	}
	got := EncodeString([]byte("a\nb"))
	if !strings.HasPrefix(string(got), "{3}\r\n") {
		t.Fatalf("control char must force literal, got %q", got)
	}
}

func TestEncodeList(t *testing.T) {
	got := string(EncodeList(EncodeAtom("NIL"), EncodeString([]byte("x"))))
	if got != `(NIL "x")` {
		t.Fatalf("list encoding: %q", got)
	}
}
