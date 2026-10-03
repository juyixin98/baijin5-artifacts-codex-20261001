package core

type Count int

const Offset Count = 100

fn Tag(c: Count): int {
	return c + 1
}

fn Calc(n: int): int {
	return Tag(n + Offset)
}
