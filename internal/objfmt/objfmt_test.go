package objfmt

import (
	"bytes"
	"testing"
)

func TestRoundtrip(t *testing.T) {
	src := &Object{
		Name: "tiny.o",
		Sections: []*Section{
			{
				Name: ".text.main",
				Data: []byte{1, 2, 3, 4, 5, 6, 7, 8},
				Relocs: []Reloc{
					{Off: 4, SymIdx: 1, Addend: -4, Kind: KindAbs32},
				},
			},
			{Name: ".data.x", Data: []byte{0xAA}, Keep: true},
		},
		Symbols: []*Symbol{
			{Name: "main", Bind: BindStrong, Off: 0, Def: true, Export: true, SecIdx: 0},
			{Name: "helper", Bind: BindStrong, Off: 0},
		},
	}
	b, err := Encode(src)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	got, err := Decode(b)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.Name != "tiny.o" || len(got.Sections) != 2 || len(got.Symbols) != 2 {
		t.Fatalf("unexpected decoded header: %+v", got)
	}
	if !bytes.Equal(got.Sections[0].Data, src.Sections[0].Data) {
		t.Fatalf("section data mismatch")
	}
	rl := got.Sections[0].Relocs[0]
	if rl.Off != 4 || rl.SymIdx != 1 || rl.Addend != -4 {
		t.Fatalf("reloc mismatch: %+v", rl)
	}
	if got.Symbols[0].Bind != BindStrong || !got.Symbols[0].Export || !got.Symbols[0].Def {
		t.Fatalf("symbol flags mismatch: %+v", got.Symbols[0])
	}
	if !got.Sections[1].Keep {
		t.Fatalf("keep flag lost")
	}
}

func TestDecodeErrors(t *testing.T) {
	if _, err := Decode([]byte("NOPE\x01\x00")); err == nil {
		t.Fatal("expected bad magic error")
	}
	if _, err := Decode(append([]byte(Magic), 0x02, 0x00)); err == nil {
		t.Fatal("expected unsupported version error")
	}
	if _, err := Decode(append([]byte(Magic), 0x01, 0x00)); err == nil {
		t.Fatal("expected truncation error")
	}
}

func TestEncodeRejectsDanglingReloc(t *testing.T) {
	o := &Object{
		Name:     "bad.o",
		Sections: []*Section{{Name: ".t", Data: []byte{0, 0, 0, 0}, Relocs: []Reloc{{Off: 0, SymIdx: 7}}}},
	}
	if _, err := Encode(o); err == nil {
		t.Fatal("expected error for reloc referencing missing symbol")
	}
}
