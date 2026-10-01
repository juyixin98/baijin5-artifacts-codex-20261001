//! Minimal line-delimited TCP client for service examples.
//! Usage: echo '<json>' | cargo run --example tcp_client -- --addr 127.0.0.1:8147

use std::io::{self, Read, Write};
use std::net::TcpStream;

fn main() {
    let mut addr = "127.0.0.1:8140".to_string();
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        if a == "--addr" {
            addr = args.next().expect("--addr needs a value");
        }
    }

    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    let line = input.trim();
    if line.is_empty() {
        eprintln!("empty request line");
        std::process::exit(2);
    }

    let mut stream = TcpStream::connect(&addr).expect("connect to fologic serve");
    stream.write_all(line.as_bytes()).expect("write request");
    stream.write_all(b"\n").expect("write newline");
    // Half-close the write side so a line-oriented server sees end of request.
    stream
        .shutdown(std::net::Shutdown::Write)
        .expect("shutdown write");

    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read response");
    println!("{}", response.trim());
}
