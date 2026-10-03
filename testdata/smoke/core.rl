package core

type Age int

const Base Age = 10

fn Add[A](x: A, y: A): A {
	return x + y
}

fn Bump(n: int): int {
	if n > 0 {
		return Add[int](n, Base)
	} else {
		return n
	}
}
