package core

type Label str

const Offset int = 100

fn Tag(c: Label): str {
	return c
}

fn Calc(n: int): int {
	return n + Offset + 1
}
