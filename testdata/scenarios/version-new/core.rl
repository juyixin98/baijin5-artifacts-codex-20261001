package core

type Count int

const Offset Count = 100

fn Scale[T](a: T, b: T): T {
	return a * b
}

fn secret(x: int): int {
	return x + 1
}

fn Calc(n: int): int {
	return Scale[int](n, Offset) + secret(n)
}
