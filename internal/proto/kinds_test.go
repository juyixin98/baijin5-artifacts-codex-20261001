package proto

import (
	"errors"
	"testing"

	"sockswhitelist/internal/wire"
)

func TestReplyCodeMapping(t *testing.T) {
	cases := map[Kind]byte{
		KindPolicyDenied:    wire.RepConnectionNotAllowed,
		KindResolveFailed:   wire.RepHostUnreachable,
		KindDialRefused:     wire.RepConnectionRefused,
		KindDialUnreachable: wire.RepNetworkUnreachable,
		KindDialTimeout:     wire.RepTTLExpired,
		KindRelayPeerReset:  wire.RepGeneralFailure,
	}
	for k, want := range cases {
		if got := ReplyCode(k); got != want {
			t.Errorf("ReplyCode(%s)=0x%02x want 0x%02x", k, got, want)
		}
	}
}

func TestFailureClassification(t *testing.T) {
	f := NewFailure(KindPolicyDenied, "ip %s rejected", "1.2.3.4")
	if f.Kind != KindPolicyDenied || f.Error() == "" {
		t.Fatalf("bad failure: %v", f)
	}
	wrapped := AsFailure(f)
	if wrapped != f {
		t.Fatal("AsFailure must preserve an already classified failure")
	}
	plain := AsFailure(errors.New("boom"))
	if plain.Kind != KindInternal {
		t.Fatalf("unclassified error must map to internal, got %s", plain.Kind)
	}
}
