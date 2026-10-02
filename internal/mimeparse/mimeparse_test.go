package mimeparse

import (
	"bytes"
	"strings"
	"testing"
)

const multipartMsg = "Message-ID: <x@y>\r\n" +
	"Subject: Multi\r\n" +
	"From: A <a@x>\r\n" +
	"To: B <b@x>\r\n" +
	"Content-Type: multipart/alternative; boundary=\"B-1\"\r\n" +
	"\r\n" +
	"preamble\r\n" +
	"--B-1\r\n" +
	"Content-Type: text/plain; charset=utf-8\r\n" +
	"\r\n" +
	"plain body line\r\n" +
	"--B-1\r\n" +
	"Content-Type: text/html; charset=utf-8\r\n" +
	"\r\n" +
	"<p>html</p>\r\n" +
	"--B-1--\r\n" +
	"epilogue"

func TestParseMultipartTree(t *testing.T) {
	e, err := Parse([]byte(multipartMsg))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if e.Type != "multipart" || e.Subtype != "alternative" {
		t.Fatalf("top type want multipart/alternative, got %s/%s", e.Type, e.Subtype)
	}
	if len(e.Children) != 2 {
		t.Fatalf("want 2 parts, got %d", len(e.Children))
	}
	if e.Children[0].Type != "text" || e.Children[0].Subtype != "plain" {
		t.Fatalf("part1 want text/plain, got %s/%s", e.Children[0].Type, e.Children[0].Subtype)
	}
}

func TestExtractWholeIsRaw(t *testing.T) {
	e, _ := Parse([]byte(multipartMsg))
	raw, err := e.Extract(SectionSpec{Kind: SecWhole}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(raw, []byte(multipartMsg)) {
		t.Fatalf("BODY[] must return exact raw bytes")
	}
}

func TestExtractPartByPath(t *testing.T) {
	e, _ := Parse([]byte(multipartMsg))
	spec, _, err := ParseSection("1", nil)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := e.Extract(spec, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "plain body line") {
		t.Fatalf("part1 text missing: %q", raw)
	}
	if strings.Contains(string(raw), "html") {
		t.Fatalf("part1 leaked part2: %q", raw)
	}
}

func TestExtractHeaderFields(t *testing.T) {
	msg := []byte("Subject: Keep-Me\r\nX-Secret: hidden\r\nDate: d\r\n\r\nbody")
	e, _ := Parse(msg)
	spec, _, err := ParseSection("HEADER.FIELDS (SUBJECT)", nil)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := e.Extract(spec, nil)
	if err != nil {
		t.Fatal(err)
	}
	s := string(raw)
	if !strings.Contains(s, "Subject: Keep-Me") {
		t.Fatalf("subject missing: %q", s)
	}
	if strings.Contains(s, "X-Secret") {
		t.Fatalf("non-selected header leaked: %q", s)
	}
}

func TestExtractPartialByteRange(t *testing.T) {
	msg := []byte("Subject: s\r\n\r\n0123456789")
	e, _ := Parse(msg)
	spec, partial, err := ParseSection("TEXT", []byte("<2.4>"))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := e.Extract(spec, partial)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "2345" {
		t.Fatalf("partial <2.4> want 2345, got %q", raw)
	}
}

func TestNonexistentPartIsOmitted(t *testing.T) {
	e, _ := Parse([]byte(multipartMsg))
	spec, _, _ := ParseSection("9", nil)
	raw, err := e.Extract(spec, nil)
	if err != nil {
		t.Fatalf("nonexistent part should not error: %v", err)
	}
	if raw != nil {
		t.Fatalf("nonexistent part should be nil (item omitted), got %q", raw)
	}
}

func TestEnvelopeRoundTrip(t *testing.T) {
	msg := []byte("Date: Mon, 01 Sep 2025 08:00:00 +0000\r\n" +
		"From: Jane Doe <jane@example.com>\r\n" +
		"Subject: Hello\r\n" +
		"To: Bob <bob@example.com>\r\n" +
		"Message-ID: <abc@example.com>\r\n\r\nbody")
	e, _ := Parse(msg)
	env := e.Envelope()
	if env.Subject != "Hello" {
		t.Fatalf("subject got %q", env.Subject)
	}
	if len(env.From) != 1 || env.From[0].Name != "Jane Doe" ||
		env.From[0].Mailbox != "jane" || env.From[0].Host != "example.com" {
		t.Fatalf("from parse wrong: %+v", env.From)
	}
	if string(env.MessageID) != "<abc@example.com>" {
		t.Fatalf("message-id got %q", env.MessageID)
	}
	enc := string(env.Encode())
	if !strings.Contains(enc, `"jane"`) || !strings.Contains(enc, `"example.com"`) {
		t.Fatalf("envelope encoding missing address parts: %s", enc)
	}
}

func TestDecode2047(t *testing.T) {
	cases := map[string]string{
		"=?UTF-8?B?RnJhbmM=?=":     "Franc",
		"=?UTF-8?Q?R=C3=A9union?=": "Réunion",
		"plain":                    "plain",
	}
	for in, want := range cases {
		if got := decode2047(in); got != want {
			t.Fatalf("decode2047(%q)=%q want %q", in, got, want)
		}
	}
}

func TestUnknownSectionRejected(t *testing.T) {
	if _, _, err := ParseSection("BOGUS", nil); err == nil {
		t.Fatal("BOGUS section should be rejected")
	}
}

func TestBodyStructureShape(t *testing.T) {
	e, _ := Parse([]byte(multipartMsg))
	bs := string(e.BodyStructure())
	// Multipart: (part1 part2 "alternative").
	if !strings.HasPrefix(bs, "((") || !strings.Contains(bs, `"text"`) ||
		!strings.Contains(bs, `"plain"`) || !strings.Contains(bs, `"html"`) ||
		!strings.HasSuffix(strings.TrimSpace(bs), `)`) {
		t.Fatalf("bodystructure shape wrong: %s", bs)
	}
	if !strings.Contains(bs, `"alternative"`) {
		t.Fatalf("multipart subtype missing: %s", bs)
	}
	// Non-extension BODY should not include disposition data markers.
	plain := e.Children[0]
	if !strings.Contains(string(plain.Body()), `"7bit"`) {
		t.Fatalf("default 7bit encoding missing: %s", plain.Body())
	}
}

func TestAttachmentDispositionAndEncoding(t *testing.T) {
	msg := "Content-Type: text/plain; name=\"n.txt\"\r\n" +
		"Content-Disposition: attachment; filename=\"n.txt\"\r\n" +
		"Content-Transfer-Encoding: base64\r\n" +
		"Content-ID: <cid-1>\r\n\r\n" +
		"QUJD"
	e, err := Parse([]byte(msg))
	if err != nil {
		t.Fatal(err)
	}
	if e.Disposition != "attachment" {
		t.Fatalf("disposition want attachment, got %q", e.Disposition)
	}
	if e.DispParams["filename"] != "n.txt" {
		t.Fatalf("filename param wrong: %v", e.DispParams)
	}
	if e.Encoding != "base64" {
		t.Fatalf("encoding want base64, got %q", e.Encoding)
	}
	if e.ID != "<cid-1>" {
		t.Fatalf("content-id wrong: %q", e.ID)
	}
	if !strings.Contains(string(e.BodyStructure()), `"attachment"`) {
		t.Fatalf("bodystructure missing disposition: %s", e.BodyStructure())
	}
}

func TestNULInHeaderIsParseError(t *testing.T) {
	_, err := Parse([]byte("Subject: bad\x00x\r\n\r\nbody"))
	if err == nil || !errorsIs(err, ParseError) {
		t.Fatalf("want ParseError, got %v", err)
	}
}

func errorsIs(err, target error) bool {
	return err != nil && (err == target || strings.Contains(err.Error(), target.Error()))
}
