# weak-undefined call links to null and must fail at runtime, not at link.
.object weaknull_main.o
.section .text.main
.weakext maybe
.globl main
.export main
main:
call maybe
halt
