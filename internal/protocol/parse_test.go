package protocol_test

import (
	"testing"

	"smtpsink/internal/protocol"
)

func TestParseReversePath(t *testing.T) {
	cases := []struct {
		name    string
		arg     string
		want    string
		wantErr bool
	}{
		{"angle addr", "<Bob@Example.COM>", "bob@example.com", false},
		{"empty bounce path", "<>", "", false},
		{"bare addr", "alice@localhost", "alice@localhost", false},
		{"with size param", "<a@b.com> SIZE=1234", "a@b.com", false},
		{"missing", "", "", true},
		{"unterminated", "<a@b.com", "", true},
		{"bad syntax", "<not an addr>", "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, params, err := protocol.ParseReversePath(tc.arg)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("expected error for %q", tc.arg)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Fatalf("addr = %q want %q", got, tc.want)
			}
			if tc.name == "with size param" && params["SIZE"] != "1234" {
				t.Fatalf("SIZE param = %q", params["SIZE"])
			}
		})
	}
}

func TestParseForwardPath(t *testing.T) {
	if _, _, err := protocol.ParseForwardPath("<>"); err == nil {
		t.Fatal("empty forward path must be rejected")
	}
	got, _, err := protocol.ParseForwardPath("<USER@Sink.LOCAL>")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "user@sink.local" {
		t.Fatalf("normalized = %q", got)
	}
	if _, _, err := protocol.ParseForwardPath("<a@b.com> BOGUS=1"); err == nil {
		t.Fatal("unknown RCPT parameter must be rejected")
	}
}

func TestValidMailbox(t *testing.T) {
	good := []string{"a@b.com", "a.b+c@sub.example", "x_y@localhost"}
	bad := []string{"", "no-at", "@b.com", "a@", "a..b@c.com", ".a@b.com", "a@b..c.com", "a b@c.com"}
	for _, a := range good {
		if !protocol.ValidMailbox(a) {
			t.Errorf("expected valid: %q", a)
		}
	}
	for _, a := range bad {
		if protocol.ValidMailbox(a) {
			t.Errorf("expected invalid: %q", a)
		}
	}
}

func TestRedactAddress(t *testing.T) {
	cases := map[string]string{
		"alice@example.com": "a***e@example.com",
		"ab@x.com":          "a*@x.com",
		"a@x.com":           "a@x.com",
		"":                  "<>",
	}
	for in, want := range cases {
		if got := protocol.RedactAddress(in); got != want {
			t.Errorf("RedactAddress(%q) = %q, want %q", in, got, want)
		}
	}
}
