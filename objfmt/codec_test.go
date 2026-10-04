package objfmt

import (
	"bytes"
	"testing"
)

// TestRoundTrip is hand-authored: the expected bytes/fields are constructed
// independently rather than produced by the encoder being tested.
func TestRoundTrip(t *testing.T) {
	o := &Object{
		Name: "hand.mo",
		Sections: []Section{
			{Name: ".text", Flags: FlagAlloc, Data: []byte{0x10, 0x00, 0x00, 0x00, 0x20}},
		},
		Symbols: []Symbol{
			{Name: "main", Section: 0, Value: 0, Size: 5, Binding: BindGlobal, Export: true},
			{Name: "helper", Section: UndefinedSection, Binding: BindGlobal},
		},
		Relocs: map[uint16][]Reloc{
			0: {{Offset: 0, Type: RelAbs32, Symbol: 1, Addend: 2}},
		},
	}
	data, err := Encode(o)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if !bytes.HasPrefix(data, []byte{'M', 'O', 'B', 'J', 1, 0}) {
		t.Fatalf("bad header prefix: % x", data[:6])
	}
	got, err := Decode(data)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(got.Sections) != 1 || got.Sections[0].Name != ".text" ||
		!bytes.Equal(got.Sections[0].Data, o.Sections[0].Data) {
		t.Fatalf("sections mismatch: %+v", got.Sections)
	}
	if len(got.Symbols) != 2 || got.Symbols[0].Name != "main" || !got.Symbols[0].Export {
		t.Fatalf("symbols mismatch: %+v", got.Symbols)
	}
	r := got.Relocs[0]
	if len(r) != 1 || r[0].Type != RelAbs32 || r[0].Addend != 2 {
		t.Fatalf("relocs mismatch: %+v", r)
	}
}

func TestDecodeRejectsTruncationAndBadIndices(t *testing.T) {
	good, _ := Encode(&Object{
		Sections: []Section{{Name: ".t", Flags: FlagAlloc, Data: []byte{0, 0, 0, 0}}},
		Symbols:  []Symbol{{Name: "s", Section: 0}},
		Relocs:   map[uint16][]Reloc{0: {{Offset: 0, Type: RelAbs32, Symbol: 0}}},
	})
	cases := map[string][]byte{
		"bad magic":        append([]byte("XXXX"), good[4:]...),
		"truncated":        good[:len(good)-3],
		"trailing bytes":   append(append([]byte{}, good...), 0xAA),
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := Decode(raw); err == nil {
				t.Fatalf("%s: expected decode error", name)
			}
		})
	}
	// Reloc symbol index out of range: hand-mutate the reloc symbol u16.
	bad := append([]byte{}, good...)
	// locate the only reloc record near the tail: [u16 count=1][u32 off][u8 type][u16 sym][i32 add]
	symPos := len(bad) - 2 - 4
	bad[symPos] = 0x09
	bad[symPos+1] = 0x00
	if _, err := Decode(bad); err == nil {
		t.Fatal("expected out-of-range reloc symbol to be rejected")
	}
}
