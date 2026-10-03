module report;
import pricing;

pub generic fn first(x: T) -> T {
	return x;
}

pub fn quote(cents: int, qty: int) -> int {
	let total: int = pricing::bulk_price(cents, qty);
	return first(total) + pricing::BULK_FACTOR;
}
