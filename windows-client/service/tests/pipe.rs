//! The real named pipe, against the real service loop, with no administrator rights. Asking for anything privileged can only fail safely, and bad requests never get
//! as far as the service manager. It never touches another tunnel: the only service name in this program is Kiwi's own.
#![cfg(windows)]

use kiwivpn::protocol::{Request, State};
use kiwivpn::windows::{ask_unverified as ask, open_pipe, serve_for_tests, TUNNEL_NAME};
use std::io::Write;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Duration;

fn start() {
    let stop = Arc::new(AtomicBool::new(false));
    std::thread::spawn(move || serve_for_tests(stop));
    std::thread::sleep(Duration::from_millis(300));
}

fn raw(line: &str) -> String {
    let mut pipe = open_pipe().unwrap();
    pipe.write_all(line.as_bytes()).unwrap();
    pipe.write_all(b"\n").unwrap();
    let mut out = String::new();
    std::io::BufRead::read_line(&mut std::io::BufReader::new(pipe), &mut out).unwrap();
    out
}

#[test]
fn the_pipe_answers_and_refuses_what_it_should() {
    start();
    // a hostile config is refused before anything else happens
    let s = ask(&Request::connect("[Interface]\nPostUp = calc.exe\n")).unwrap();
    assert_eq!(s.state, State::Failed);
    assert!(s.error.unwrap().contains("isn't allowed"));
    // commands that don't exist, or extra fields on ones that do
    for bad in [r#"{"cmd":"run","path":"calc.exe"}"#, r#"{"cmd":"status","path":"C:\\x"}"#, "not json"] {
        let out = raw(bad);
        assert!(out.contains("failed") && out.contains("not understood"), "{bad}: {out}");
    }
    // without administrator rights it can't touch services, and says so
    let s = ask(&Request::Status).unwrap();
    assert!(s.state == State::Failed || s.state == State::Disconnected, "{s:?}");
}

#[test]
fn it_only_ever_names_its_own_tunnel() {
    assert_eq!(TUNNEL_NAME, "kiwi");
    let source = include_str!("../src/windows.rs");
    assert!(!source.contains("client1"));
    assert_eq!(source.matches("WireGuardTunnel$").count(), 1);
}
