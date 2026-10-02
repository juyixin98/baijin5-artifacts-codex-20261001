package stun

import "testing"

func TestEncodeDecodeType(t *testing.T) {
	cases := []struct {
		name     string
		method   Method
		class    Class
		wantWire uint16
	}{
		{"binding request", MethodBinding, ClassRequest, 0x0001},
		{"binding success", MethodBinding, ClassSuccessResponse, 0x0101},
		{"binding error", MethodBinding, ClassErrorResponse, 0x0111},
		{"binding indication", MethodBinding, ClassIndication, 0x0011},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			wire := EncodeType(tc.method, tc.class)
			if wire != tc.wantWire {
				t.Fatalf("EncodeType = 0x%04x, want 0x%04x", wire, tc.wantWire)
			}
			m, c := DecodeType(wire)
			if m != tc.method || c != tc.class {
				t.Fatalf("DecodeType(0x%04x) = (%04x,%04x), want (%04x,%04x)",
					wire, uint16(m), uint16(c), uint16(tc.method), uint16(tc.class))
			}
		})
	}
}

func TestComprehensionRequiredClassification(t *testing.T) {
	required := []AttributeType{AttrMappedAddress, AttrUsername, AttrMessageIntegrity,
		AttrErrorCode, AttrXORMappedAddress, 0x0000, 0x7fff}
	optional := []AttributeType{AttrUnknownAttributes, AttrSoftware, AttrFingerprint, 0x8000, 0xffff}
	for _, at := range required {
		if !at.IsComprehensionRequired() {
			t.Errorf("attr 0x%04x must be comprehension-required", uint16(at))
		}
	}
	for _, at := range optional {
		if at.IsComprehensionRequired() {
			t.Errorf("attr 0x%04x must be comprehension-optional", uint16(at))
		}
	}
}
