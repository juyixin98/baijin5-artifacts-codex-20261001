module pricing;

// Bulk discount factor. Inlined at every use, so changing this public const
// is an INTERFACE change for functions that bake it in.
pub const BULK_FACTOR: int = 2;

// Private base margin: used only inside this module. Changing its body does
// not change any public fingerprint.
const BASE: int = 50;

fn private_adjust(x: int) -> int {
	return x + BASE;
}

pub fn unit_price(cents: int) -> int {
	return private_adjust(cents);
}

pub fn bulk_price(cents: int, qty: int) -> int {
	return unit_price(cents) * qty * BULK_FACTOR;
}
