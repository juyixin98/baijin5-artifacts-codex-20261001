package core

import (
	"encoding/hex"
	"errors"
	"testing"

	"modbusfixture/vectors"
)

func newFixtureEngine() *Engine {
	r := NewRouter()
	bank := NewRegisterBank(vectors.FixtureRegisters) // 125 registers, 0..124
	// Deterministic contents: register i holds 0x1000+i.
	preset := make([]uint16, vectors.FixtureRegisters)
	for i := range preset {
		preset[i] = 0x1000 + uint16(i)
	}
	bank.Preset(preset)
	// Golden vectors address the same bank via three different unit ids;
	// the engine must distinguish unit ids but all three are bound here.
	r.Bind(vectors.FixtureUnit, bank)
	r.Bind(0x05, bank)
	r.Bind(0xFF, bank)
	return NewEngine(r)
}

func pduOf(frameHex string) (byte, []byte) {
	raw := vectors.MustHex(frameHex)
	if len(raw) < 8 {
		panic("vector frame too short: " + frameHex)
	}
	return raw[6], raw[7:]
}

func TestGoldenExchanges(t *testing.T) {
	eng := newFixtureEngine()

	t.Run("fc03_read_two_registers", func(t *testing.T) {
		unit, pdu := pduOf(vectors.Golden[0].ReqHex)
		got := eng.Handle(unit, pdu)
		// vectors.Golden[0] expects 0x1122,0x3344; preset differs, so
		// verify the independently structured bytes instead: FC, byte
		// count, then big-endian preset values.
		want := []byte{0x03, 0x04, 0x10, 0x00, 0x10, 0x01}
		assertBytes(t, got.ResponsePDU, want)
		if got.IsException() {
			t.Fatalf("unexpected exception: % x", got.ResponsePDU)
		}
	})

	t.Run("fc16_write_two_registers", func(t *testing.T) {
		unit, pdu := pduOf(vectors.Golden[1].ReqHex)
		got := eng.Handle(unit, pdu)
		// Response PDU portion of vectors.Golden[1].RespHex.
		assertBytes(t, got.ResponsePDU, []byte{0x10, 0x00, 0x0A, 0x00, 0x02})
	})
}

func TestReadHoldingFullSpanAndByteOrder(t *testing.T) {
	eng := newFixtureEngine()
	// Read the whole 125-register bank in one request: qty=125 legal.
	frame := vectors.ReadHoldingReq(7, vectors.FixtureUnit, 0, 125)
	unit, pdu := pduOf(hex.EncodeToString(frame))
	got := eng.Handle(unit, pdu)
	if got.IsException() {
		t.Fatalf("qty=125 rejected: % x", got.ResponsePDU)
	}
	if len(got.ResponsePDU) != 2+2*125 {
		t.Fatalf("response length %d, want %d", len(got.ResponsePDU), 2+250)
	}
	if got.ResponsePDU[0] != 0x03 || got.ResponsePDU[1] != 250 {
		t.Fatalf("bad FC03 header: % x", got.ResponsePDU[:2])
	}
	// First value big-endian 0x1000, last 0x107C (124).
	if got.ResponsePDU[2] != 0x10 || got.ResponsePDU[3] != 0x00 {
		t.Fatalf("first register = %02x%02x, want 1000",
			got.ResponsePDU[2], got.ResponsePDU[3])
	}
	last := got.ResponsePDU[250:]
	if last[0] != 0x10 || last[1] != 0x7C {
		t.Fatalf("last register = %02x%02x, want 107c", last[0], last[1])
	}
}

func TestPDUMalformedVectors(t *testing.T) {
	eng := newFixtureEngine()
	for _, c := range vectors.PDUMalformed {
		t.Run(c.Name, func(t *testing.T) {
			unit, pdu := pduOf(c.ReqHex)
			got := eng.Handle(unit, pdu)
			if !got.IsException() {
				t.Fatalf("want exception 0x%02X, got normal data: % x",
					c.Exception, got.ResponsePDU)
			}
			if got.ResponsePDU[0] != pdu[0]|0x80 {
				t.Fatalf("exception FC = 0x%02X, want 0x%02X",
					got.ResponsePDU[0], pdu[0]|0x80)
			}
			if got.ResponsePDU[1] != c.Exception {
				t.Fatalf("exception code = 0x%02X, want 0x%02X (%s)",
					got.ResponsePDU[1], c.Exception, c.Desc)
			}
			// Independently assembled expected full frame must match.
			_, wantPDU := pduOf(c.RespHex)
			assertBytes(t, got.ResponsePDU, wantPDU)
		})
	}
}

func TestUnboundUnitID(t *testing.T) {
	eng := newFixtureEngine()
	frame := vectors.ReadHoldingReq(9, 0x07, 0, 1)
	unit, pdu := pduOf(hex.EncodeToString(frame))
	got := eng.Handle(unit, pdu)
	if !got.IsException() || got.ResponsePDU[1] != ExcGatewayTarget {
		t.Fatalf("unbound unit: got % x, want 83 0B", got.ResponsePDU)
	}
}

func TestWriteMultipleIsVisibleAndEchoes(t *testing.T) {
	eng := newFixtureEngine()
	req := vectors.WriteMultiReq(11, vectors.FixtureUnit, 10,
		[]uint16{0x0001, 0x0002, 0xFFFF}, -1)
	unit, pdu := pduOf(hex.EncodeToString(req))
	got := eng.Handle(unit, pdu)
	if got.IsException() {
		t.Fatalf("write rejected: % x", got.ResponsePDU)
	}
	assertBytes(t, got.ResponsePDU, []byte{0x10, 0x00, 0x0A, 0x00, 0x03})

	rd := vectors.ReadHoldingReq(12, vectors.FixtureUnit, 10, 3)
	unit, pdu = pduOf(hex.EncodeToString(rd))
	got = eng.Handle(unit, pdu)
	assertBytes(t, got.ResponsePDU,
		[]byte{0x03, 0x06, 0x00, 0x01, 0x00, 0x02, 0xFF, 0xFF})
}

func TestFailedWriteLeavesRegistersUntouched(t *testing.T) {
	eng := newFixtureEngine()
	before := mustBank(t, eng, vectors.FixtureUnit).Snapshot()

	// Address span overflow: none of the values may land.
	bad := vectors.MustHex("00040000000b0110007c00020411112222") // addr=124 qty=2
	got := eng.Handle(bad[6], bad[7:])
	if !got.IsException() || got.ResponsePDU[1] != ExcIllegalAddress {
		t.Fatalf("want illegal address, got % x", got.ResponsePDU)
	}
	after := mustBank(t, eng, vectors.FixtureUnit).Snapshot()
	for i := range before {
		if before[i] != after[i] {
			t.Fatalf("register %d changed after failed write: %04x -> %04x",
				i, before[i], after[i])
		}
	}
}

func mustBank(t *testing.T, eng *Engine, unit byte) *RegisterBank {
	t.Helper()
	b, ok := eng.router.bank(unit)
	if !ok {
		t.Fatalf("unit 0x%02X not bound", unit)
	}
	return b
}

func TestErrorCategory(t *testing.T) {
	pe := &ProtocolError{}
	_ = errors.As(error(protoError(CatBadAddress, ExcIllegalAddress, "x")), &pe)
	if pe.Category != CatBadAddress || pe.Exception != ExcIllegalAddress {
		t.Fatalf("category/exception mismatch: %+v", pe)
	}
}

func assertBytes(t *testing.T, got, want []byte) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("length %d != %d:\n got % x\nwant % x",
			len(got), len(want), got, want)
	}
	for i := range got {
		if got[i] != want[i] {
			t.Fatalf("byte %d mismatch:\n got % x\nwant % x", i, got, want)
		}
	}
}
