//! Signing in through the person's own browser, so the app never sees a password. A random verifier stays here; the page gets only its hash, and hands a one-time code back
//! to 127.0.0.1. The backend swaps code + verifier for a session limited to the VPN. See backend/app/api/routes/desktop.py.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

pub const SIGN_IN_MINUTES: u64 = 5;

fn random_text(bytes: usize) -> Result<String, String> {
    let mut raw = vec![0u8; bytes];
    getrandom::getrandom(&mut raw).map_err(|e| format!("Couldn't start signing in: {e}"))?;
    Ok(URL_SAFE_NO_PAD.encode(raw))
}

pub struct Attempt {
    pub verifier: String,
    pub challenge: String,
    pub state: String,
}

impl Attempt {
    pub fn new() -> Result<Self, String> {
        let verifier = random_text(32)?;
        let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
        Ok(Attempt { verifier, challenge, state: random_text(24)? })
    }

    pub fn page(&self, base: &str, port: u16) -> String {
        format!("{base}/vpn/desktop?challenge={}&port={port}&state={}", self.challenge, self.state)
    }
}

fn unescape(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() + 0 && i + 2 <= bytes.len() - 1 => {
                match u8::from_str_radix(std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("zz"), 16) {
                    Ok(b) => {
                        out.push(b);
                        i += 3;
                        continue;
                    }
                    Err(_) => out.push(b'%'),
                }
            }
            b'+' => out.push(b' '),
            b => out.push(b),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The code from the browser's request line, only if it's our callback and carries our state.
pub fn read_callback(request_line: &str, state: &str) -> Option<String> {
    let mut parts = request_line.split_whitespace();
    if parts.next()? != "GET" {
        return None;
    }
    let target = parts.next()?;
    let query = target.strip_prefix("/callback?")?;
    let (mut code, mut got_state) = (None, None);
    for pair in query.split('&') {
        match pair.split_once('=') {
            Some(("code", v)) => code = Some(unescape(v)),
            Some(("state", v)) => got_state = Some(unescape(v)),
            _ => {}
        }
    }
    if got_state.as_deref() != Some(state) {
        return None;
    }
    code.filter(|c| (20..=100).contains(&c.len()))
}

const PAGE: &str = "<!doctype html><meta charset=utf-8><title>Kiwi VPN</title><body style=\"background:#070b09;color:#f6f8f4;font:18px system-ui;display:grid;place-items:center;height:90vh\"><p>You're signed in. You can close this tab and go back to Kiwi VPN.</p>";

fn answer(stream: &mut TcpStream, ok: bool) {
    let (status, body) = if ok { ("200 OK", PAGE) } else { ("400 Bad Request", "This link isn't valid.") };
    let reply = format!("HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}", body.len());
    let _ = stream.write_all(reply.as_bytes());
}

fn first_line(stream: &mut TcpStream) -> Option<String> {
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    let mut data = Vec::new();
    let mut chunk = [0u8; 1024];
    while data.len() < 8192 {
        let got = stream.read(&mut chunk).ok()?;
        if got == 0 {
            break;
        }
        data.extend_from_slice(&chunk[..got]);
        if data.windows(2).any(|w| w == b"\r\n") {
            break;
        }
    }
    String::from_utf8_lossy(&data).lines().next().map(str::to_string)
}

/// A listener on a port Windows picks, on the loopback address only.
pub fn listen() -> Result<(TcpListener, u16), String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| format!("Couldn't start signing in: {e}"))?;
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    Ok((listener, port))
}

/// Waits for the browser. Anything else knocking on the port gets refused and the wait goes on.
pub fn wait_for_code(listener: &TcpListener, state: &str, cancel: &AtomicBool, limit: Duration) -> Result<String, String> {
    let started = Instant::now();
    loop {
        if cancel.load(Ordering::SeqCst) {
            return Err("Signing in was cancelled.".into());
        }
        if started.elapsed() > limit {
            return Err("Signing in took too long. Please try again.".into());
        }
        match listener.accept() {
            Ok((mut stream, _)) => {
                let _ = stream.set_nonblocking(false);
                let code = first_line(&mut stream).and_then(|line| read_callback(&line, state));
                answer(&mut stream, code.is_some());
                if let Some(code) = code {
                    return Ok(code);
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(150)),
            Err(e) => return Err(format!("Couldn't finish signing in: {e}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_challenge_is_what_the_website_and_backend_expect() {
        let a = Attempt::new().unwrap();
        assert_eq!(a.verifier.len(), 43);
        assert_eq!(a.challenge.len(), 43, "the page's readRequest wants exactly 43");
        assert!((16..=64).contains(&a.state.len()), "and a state of 16 to 64");
        for text in [&a.challenge, &a.state] {
            assert!(text.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
        }
        assert_eq!(a.challenge, URL_SAFE_NO_PAD.encode(Sha256::digest(a.verifier.as_bytes())));
        assert_eq!(a.page("https://x", 50123), format!("https://x/vpn/desktop?challenge={}&port=50123&state={}", a.challenge, a.state));
    }

    #[test]
    fn only_our_callback_with_our_state_gives_a_code() {
        let code = "c".repeat(43);
        assert_eq!(read_callback(&format!("GET /callback?code={code}&state=abc HTTP/1.1"), "abc"), Some(code.clone()));
        assert_eq!(read_callback(&format!("GET /callback?state=abc&code={code} HTTP/1.1"), "abc"), Some(code.clone()));
        for bad in [format!("GET /callback?code={code}&state=other HTTP/1.1"), format!("GET /callback?code={code} HTTP/1.1"), format!("POST /callback?code={code}&state=abc HTTP/1.1"), format!("GET /other?code={code}&state=abc HTTP/1.1"), "GET /callback?code=short&state=abc HTTP/1.1".into(), "".into()] {
            assert_eq!(read_callback(&bad, "abc"), None, "{bad}");
        }
    }

    #[test]
    fn the_real_listener_takes_the_right_request_and_refuses_the_wrong_one() {
        let (listener, port) = listen().unwrap();
        let code = "k".repeat(43);
        let send = move |line: String| {
            let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
            s.write_all(format!("{line}\r\nHost: x\r\n\r\n").as_bytes()).unwrap();
            let mut out = String::new();
            s.read_to_string(&mut out).unwrap();
            out
        };
        let good = format!("GET /callback?code={code}&state=mine HTTP/1.1");
        let client = std::thread::spawn(move || {
            let refused = send("GET /callback?code=wrongwrongwrongwrongwrong&state=theirs HTTP/1.1".into());
            let accepted = send(good);
            (refused, accepted)
        });
        let got = wait_for_code(&listener, "mine", &AtomicBool::new(false), Duration::from_secs(10)).unwrap();
        assert_eq!(got, code);
        let (refused, accepted) = client.join().unwrap();
        assert!(refused.starts_with("HTTP/1.1 400"), "{refused}");
        assert!(accepted.starts_with("HTTP/1.1 200") && accepted.contains("signed in"));
    }

    #[test]
    fn cancelling_stops_the_wait() {
        let (listener, _) = listen().unwrap();
        assert!(wait_for_code(&listener, "x", &AtomicBool::new(true), Duration::from_secs(10)).unwrap_err().contains("cancelled"));
    }

    #[test]
    fn percent_encoding_reads_correctly() {
        assert_eq!(unescape("a%2Db"), "a-b");
        assert_eq!(unescape("abc%"), "abc%");
        assert_eq!(unescape("%zz1"), "%zz1");
    }
}
