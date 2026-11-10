//! Kiwi VPN 2.0's measurements, on request only: server timing for Smart Connect, the speed test and the leak check.
//! They run in the app (as the signed-in person, not SYSTEM) and send nothing about the person: no account, no device, no key.

use serde::Serialize;
use std::io::Read;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream, ToSocketAddrs, UdpSocket};
use std::sync::Arc;
use std::time::{Duration, Instant};

const SPEED_HOST: &str = "speed.cloudflare.com";
const DOWN_BYTES: u64 = 25_000_000;
const UP_BYTES: usize = 5_000_000;

fn agent(timeout: Duration) -> Result<ureq::Agent, String> {
    let tls = native_tls::TlsConnector::new().map_err(|e| e.to_string())?;
    Ok(ureq::AgentBuilder::new().tls_connector(Arc::new(tls)).timeout_connect(Duration::from_secs(3)).timeout(timeout).redirects(0).build())
}

/// A host name as the backend gives it for a proxy: letters, digits, dots and dashes only, so it can't become another URL.
pub fn valid_host(h: &str) -> bool {
    !h.is_empty() && h.len() <= 253 && h.contains('.') && h.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
}

pub fn median(mut v: Vec<f64>) -> Option<f64> {
    v.retain(|x| x.is_finite());
    if v.is_empty() {
        return None;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let m = v.len() / 2;
    Some(if v.len() % 2 == 1 { v[m] } else { (v[m - 1] + v[m]) / 2.0 })
}

/// Median ms of 3 HTTPS requests to a server's proxy name (it answers a direct request with a quick 404 and no data).
/// Any HTTP answer counts as reached; a network error is no sample.
pub fn time_host(host: &str) -> Option<f64> {
    if !valid_host(host) {
        return None;
    }
    let a = agent(Duration::from_millis(2500)).ok()?;
    let mut samples = Vec::new();
    for i in 0..3 {
        let t = Instant::now();
        match a.get(&format!("https://{host}/kiwi-ping?{i}")).call() {
            Ok(_) | Err(ureq::Error::Status(_, _)) => samples.push(t.elapsed().as_secs_f64() * 1000.0),
            Err(_) => {}
        }
    }
    median(samples)
}

/// Every server at once (one thread each), so Smart Connect waits for the slowest, not the sum.
pub fn probe(servers: &[(i64, String)]) -> Vec<(i64, Option<f64>)> {
    let handles: Vec<_> = servers.iter().cloned().map(|(id, host)| std::thread::spawn(move || (id, time_host(&host)))).collect();
    handles.into_iter().filter_map(|h| h.join().ok()).collect()
}

// ----- 2.1: timing a server without a proxy name -----

/// The TCP port timed on a server's endpoint address: its HTTPS proxy's, where it runs one. Only a completed connection counts
/// (Windows retries a refused connection for about a second, so a refusal's time says nothing about distance).
pub const TIMING_PORT: u16 = 443;

/// An endpoint host as a config gives it: an IP address or a plain host name, nothing that could become a path or URL.
pub fn valid_endpoint_host(h: &str) -> bool {
    h.parse::<IpAddr>().is_ok() || valid_host(h)
}

/// Only public unicast addresses are timed, so a server list can never point Smart Connect at the person's own network.
pub fn public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(a) => {
            let o = a.octets();
            let shared = o[0] == 100 && (o[1] & 0xc0) == 64; // 100.64.0.0/10, carrier-grade NAT
            !(a.is_private() || a.is_loopback() || a.is_link_local() || a.is_broadcast() || a.is_documentation() || a.is_unspecified() || a.is_multicast() || shared || o[0] == 0 || o[0] >= 240)
        }
        IpAddr::V6(a) => {
            let first = a.segments()[0];
            !(a.is_loopback() || a.is_unspecified() || a.is_multicast() || (first & 0xfe00) == 0xfc00 || (first & 0xffc0) == 0xfe80)
        }
    }
}

/// Median ms of 3 TCP connection set-ups to `addr` (the server's SYN-ACK; closed at once, nothing sent). Silence is no sample.
pub fn time_tcp(addr: SocketAddr, limit: Duration) -> Option<f64> {
    let mut samples = Vec::new();
    for _ in 0..3 {
        let t = Instant::now();
        if let Ok(stream) = TcpStream::connect_timeout(&addr, limit) {
            samples.push(t.elapsed().as_secs_f64() * 1000.0);
            drop(stream);
        }
    }
    median(samples)
}

/// A server's endpoint (from this PC's own config for it), timed by TCP to its proxy port. Public addresses only.
pub fn time_endpoint(host: &str) -> Option<f64> {
    if !valid_endpoint_host(host) {
        return None;
    }
    let addr = (host, TIMING_PORT).to_socket_addrs().ok()?.find(|a| public_ip(a.ip()))?;
    time_tcp(addr, Duration::from_millis(1500))
}

/// What Smart Connect can time for one server: its proxy name (from the backend) and/or its endpoint (from this PC's config).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Target {
    pub proxy: Option<String>,
    pub endpoint: Option<String>,
}

/// The proxy's timing when it answers, else the endpoint's; None when neither can be timed (Smart Connect then goes by load).
pub fn time_target(t: &Target) -> Option<f64> {
    t.proxy.as_deref().and_then(time_host).or_else(|| t.endpoint.as_deref().and_then(time_endpoint))
}

/// Every server at once, one thread each.
pub fn probe_targets(targets: &[(i64, Target)]) -> Vec<(i64, Option<f64>)> {
    let handles: Vec<_> = targets.iter().cloned().map(|(id, t)| std::thread::spawn(move || (id, time_target(&t)))).collect();
    handles.into_iter().filter_map(|h| h.join().ok()).collect()
}

// ----- 2.1: is the server still answering through the tunnel? -----

/// A DNS question for pricechat.live's address (the app's own name: it tells the resolver nothing new), with the given id.
pub fn dns_question(id: u16) -> Vec<u8> {
    let mut q = vec![(id >> 8) as u8, id as u8, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0];
    for label in ["pricechat", "live"] {
        q.push(label.len() as u8);
        q.extend_from_slice(label.as_bytes());
    }
    q.extend_from_slice(&[0, 0, 1, 0, 1]);
    q
}

/// A DNS reply to that question: the same id with the reply bit set. Any reply, even an error, proves the round trip.
pub fn is_dns_answer(reply: &[u8], id: u16) -> bool {
    reply.len() >= 12 && reply[0] == (id >> 8) as u8 && reply[1] == id as u8 && reply[2] & 0x80 != 0
}

/// One liveness probe: a DNS question to each of the tunnel's resolvers, sent FROM the tunnel's own address. Windows only sends a
/// packet from an address out of that address's own interface, so the probe can't leave by any other way: with the tunnel gone the
/// bind fails, and with the server gone nothing comes back. Two tries, `wait` each. A "port unreachable" coming back also counts:
/// something answered through the tunnel.
pub fn resolver_answers(local: Ipv4Addr, resolvers: &[Ipv4Addr], port: u16, wait: Duration, tries: u32) -> bool {
    if resolvers.is_empty() {
        return false;
    }
    let Ok(sock) = UdpSocket::bind(SocketAddr::from((local, 0))) else { return false };
    for _ in 0..tries {
        let mut raw = [0u8; 2];
        let id = if getrandom::getrandom(&mut raw).is_ok() { u16::from_le_bytes(raw) } else { 0x4b56 };
        let q = dns_question(id);
        let mut sent = false;
        for r in resolvers {
            sent |= sock.send_to(&q, SocketAddr::from((*r, port))).is_ok();
        }
        if !sent {
            return false;
        }
        let end = Instant::now() + wait;
        let mut buf = [0u8; 512];
        loop {
            let left = end.saturating_duration_since(Instant::now());
            if left.is_zero() || sock.set_read_timeout(Some(left)).is_err() {
                break;
            }
            match sock.recv_from(&mut buf) {
                Ok((n, from)) => {
                    if resolvers.iter().any(|r| from.ip() == IpAddr::V4(*r)) && is_dns_answer(&buf[..n], id) {
                        return true;
                    }
                }
                Err(e) if matches!(e.kind(), std::io::ErrorKind::ConnectionReset | std::io::ErrorKind::ConnectionRefused) => return true,
                Err(_) => break, // timed out
            }
        }
    }
    false
}

/// The probe the app runs: the tunnel's resolvers on port 53, 1.4 s per try, two tries.
pub fn tunnel_answers(local: Ipv4Addr, resolvers: &[Ipv4Addr]) -> bool {
    resolver_answers(local, resolvers, 53, Duration::from_millis(1400), 2)
}

#[derive(Debug, Serialize, PartialEq)]
pub struct SpeedReport {
    pub ping_ms: Option<f64>,
    pub down_bytes: u64,
    pub down_ms: u64,
    pub up_bytes: u64,
    pub up_ms: u64,
    pub via: &'static str,
}

/// Through whatever connection is up: with the VPN on, that's the tunnel. About 25 MB down and 5 MB up.
pub fn speed_test() -> Result<SpeedReport, String> {
    let a = agent(Duration::from_secs(60))?;
    let mut pings = Vec::new();
    for _ in 0..3 {
        let t = Instant::now();
        if a.get(&format!("https://{SPEED_HOST}/__down?bytes=0")).call().is_ok() {
            pings.push(t.elapsed().as_secs_f64() * 1000.0);
        }
    }
    let t = Instant::now();
    let reply = a.get(&format!("https://{SPEED_HOST}/__down?bytes={DOWN_BYTES}")).call().map_err(|e| format!("The download test couldn't start: {e}"))?;
    let mut body = reply.into_reader().take(DOWN_BYTES + 1024);
    let mut buf = vec![0u8; 64 * 1024];
    let mut down = 0u64;
    loop {
        match body.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => down += n as u64,
            Err(e) => return Err(format!("The download test stopped: {e}")),
        }
    }
    let down_ms = t.elapsed().as_millis() as u64;
    let payload = vec![0u8; UP_BYTES];
    let t = Instant::now();
    a.post(&format!("https://{SPEED_HOST}/__up")).set("Content-Type", "application/octet-stream").send_bytes(&payload).map_err(|e| format!("The upload test failed: {e}"))?;
    let up_ms = t.elapsed().as_millis() as u64;
    Ok(SpeedReport { ping_ms: median(pings), down_bytes: down, down_ms, up_bytes: UP_BYTES as u64, up_ms, via: SPEED_HOST })
}

#[derive(Debug, Serialize, PartialEq)]
pub struct LeakReport {
    pub ipv4: Option<String>,
    pub ipv6: Option<String>,
    /// resolvers seen, as "address (provider)"
    pub dns: Vec<String>,
    pub tunnel_ip: Option<String>,
    /// what the VPN should show: an address, or a provider name such as "cloudflare" or "quad9"
    pub expected_dns: Vec<String>,
}

/// What a correct connection shows as its DNS, from the DNS the config uses and the server's address.
pub fn expected_dns(config_dns: &[String], server_ip: Option<&str>) -> Vec<String> {
    let mut out = Vec::new();
    for d in config_dns {
        let e = match d.as_str() {
            x if x.starts_with("10.") || x.starts_with("fd") => server_ip.unwrap_or("").to_string(), // the node's own resolver asks from the node
            "1.1.1.1" | "1.0.0.1" => "cloudflare".into(),
            "9.9.9.9" | "149.112.112.112" => "quad9".into(),
            other => other.to_string(),
        };
        if !e.is_empty() && !out.contains(&e) {
            out.push(e);
        }
    }
    out
}

/// IPv4 and IPv6 as sites see them, and the DNS resolvers that answer this PC's lookups. The resolvers come from bash.ws's open DNS
/// leak test (the one dnsleaktest.sh uses): a few random names under a fresh test id are looked up the ordinary way, and bash.ws
/// reports which resolvers asked its name servers for them. The lookups carry no data.
pub fn leak_check(server_ip: Option<String>, config_dns: &[String]) -> Result<LeakReport, String> {
    let a = agent(Duration::from_secs(8))?;
    let text = |url: &str| a.get(url).call().ok().and_then(|r| r.into_string().ok()).map(|s| s.trim().to_string()).filter(|s| !s.is_empty() && s.len() < 64);
    let ipv4 = text("https://api.ipify.org");
    let ipv6 = text("https://api6.ipify.org"); // reachable only if IPv6 leaves the PC
    let mut dns = Vec::new();
    if let Some(id) = text("https://bash.ws/id").filter(|id| valid_test_id(id)) {
        for n in 1..=5 {
            use std::net::ToSocketAddrs;
            let _ = format!("{n}.{id}.bash.ws:80").to_socket_addrs(); // the answer doesn't matter, only who asked
        }
        if let Ok(reply) = a.get(&format!("https://bash.ws/dnsleak/test/{id}?json")).call() {
            if let Ok(v) = reply.into_json::<serde_json::Value>() {
                dns = resolvers_in(&v);
            }
        }
    }
    // Some resolvers (the Kiwi servers' own) shorten the names they ask about, so bash.ws never sees its test names. Akamai's
    // whoami.akamai.net answers with the address of the resolver that asked, whatever the resolver does with names.
    if dns.is_empty() {
        use std::net::ToSocketAddrs;
        if let Ok(found) = "whoami.akamai.net:80".to_socket_addrs() {
            for a in found.filter(|a| a.is_ipv4()) {
                let seen = format!("{} (whoami.akamai.net)", a.ip());
                if !dns.contains(&seen) {
                    dns.push(seen);
                }
            }
        }
    }
    let expected_dns = expected_dns(config_dns, server_ip.as_deref());
    Ok(LeakReport { ipv4, ipv6, dns, tunnel_ip: server_ip, expected_dns })
}

/// bash.ws's test ids are short and alphanumeric; anything else isn't put into a host name.
pub fn valid_test_id(id: &str) -> bool {
    (8..=40).contains(&id.len()) && id.chars().all(|c| c.is_ascii_alphanumeric())
}

/// The resolvers in a bash.ws report: "address (organisation, country)" for each entry of type "dns".
pub fn resolvers_in(report: &serde_json::Value) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for e in report.as_array().into_iter().flatten().filter(|e| e["type"] == "dns") {
        let ip = e["ip"].as_str().unwrap_or("");
        if ip.is_empty() {
            continue;
        }
        let org = e["org"].as_str().or(e["asn"].as_str()).unwrap_or("");
        let cc = e["country"].as_str().unwrap_or("").to_uppercase();
        let seen = match (org.is_empty(), cc.is_empty()) {
            (true, true) => ip.to_string(),
            (false, true) => format!("{ip} ({org})"),
            (true, false) => format!("{ip} ({cc})"),
            (false, false) => format!("{ip} ({org}, {cc})"),
        };
        if !out.contains(&seen) {
            out.push(seen);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_bash_ws_report_gives_the_resolvers_only() {
        let v = serde_json::json!([
            {"ip":"20.80.174.33","country":"us","asn":"AS8075 Microsoft","org":"Microsoft Corporation","type":"ip"},
            {"ip":"162.158.1.2","country":"fi","asn":"AS13335 Cloudflare","org":"Cloudflare, Inc.","type":"dns"},
            {"ip":"2.29.149.29","country":"","org":"","type":"dns"},
            {"ip":"162.158.1.2","country":"fi","org":"Cloudflare, Inc.","type":"dns"},
            {"type":"conclusion","ip":""}
        ]);
        assert_eq!(resolvers_in(&v), vec!["162.158.1.2 (Cloudflare, Inc., FI)".to_string(), "2.29.149.29".to_string()]);
        assert!(resolvers_in(&serde_json::json!({"error":"x"})).is_empty());
        assert!(valid_test_id("695956395b60d835") && !valid_test_id("a.b") && !valid_test_id("x"));
    }

    #[test]
    fn only_plain_host_names_are_timed() {
        assert!(valid_host("ash1.vpn.pricechat.live"));
        for bad in ["", "localhost", "evil.example/path", "a.b@c.d", "x.y:8443", "ash1.vpn.pricechat.live?x"] {
            assert!(!valid_host(bad), "{bad}");
        }
    }

    #[test]
    fn only_public_endpoints_are_timed() {
        for ok in ["2.29.149.29", "178.156.252.184", "2a01:4f9:c012:1::1"] {
            assert!(public_ip(ok.parse().unwrap()), "{ok}");
        }
        for bad in ["10.0.0.1", "192.168.1.1", "172.16.0.1", "127.0.0.1", "169.254.1.1", "100.64.0.1", "0.0.0.0", "255.255.255.255", "224.0.0.1", "192.0.2.1", "::1", "fe80::1", "fd00::1", "ff02::1"] {
            assert!(!public_ip(bad.parse().unwrap()), "{bad}");
        }
        assert!(valid_endpoint_host("2.29.149.29") && valid_endpoint_host("hel1.vpn.pricechat.live") && valid_endpoint_host("2a01::1"));
        assert!(!valid_endpoint_host("evil/../x") && !valid_endpoint_host("") && !valid_endpoint_host("a b"));
        assert_eq!(time_endpoint("127.0.0.1"), None, "never this PC");
        assert_eq!(time_endpoint("192.168.1.1"), None, "never the local network");
        assert_eq!(time_target(&Target::default()), None);
    }

    #[test]
    fn a_tcp_timing_needs_a_completed_connection() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let ms = time_tcp(addr, Duration::from_millis(1500)).expect("a listening port is timed");
        assert!(ms >= 0.0 && ms < 1500.0);
        drop(listener);
        assert_eq!(time_tcp(addr, Duration::from_millis(500)), None, "a closed port is no sample");
    }

    #[test]
    fn the_dns_probe_is_a_well_formed_question_and_only_its_own_reply_counts() {
        let q = dns_question(0x1234);
        assert_eq!(&q[..4], &[0x12, 0x34, 0x01, 0x00]);
        assert_eq!(q.len(), 12 + 1 + 9 + 1 + 4 + 1 + 4);
        assert!(q.windows(9).any(|w| w == b"pricechat"));
        let mut reply = q.clone();
        reply[2] |= 0x80;
        assert!(is_dns_answer(&reply, 0x1234));
        assert!(!is_dns_answer(&q, 0x1234), "the question itself isn't an answer");
        assert!(!is_dns_answer(&reply, 0x1235), "another question's answer doesn't count");
        assert!(!is_dns_answer(&reply[..5], 0x1234));
    }

    /// A fake resolver on this PC: `mode` 0 answers, 1 never answers, 2 answers with the wrong id.
    fn fake_resolver(mode: u8) -> (u16, std::thread::JoinHandle<()>) {
        let sock = UdpSocket::bind("127.0.0.1:0").unwrap();
        let port = sock.local_addr().unwrap().port();
        sock.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        let h = std::thread::spawn(move || {
            let mut buf = [0u8; 512];
            while let Ok((n, from)) = sock.recv_from(&mut buf) {
                let mut r = buf[..n].to_vec();
                r[2] |= 0x80;
                match mode {
                    0 => { let _ = sock.send_to(&r, from); }
                    2 => { r[1] ^= 0xff; let _ = sock.send_to(&r, from); }
                    _ => {}
                }
            }
        });
        (port, h)
    }

    #[test]
    fn the_liveness_probe_says_alive_only_when_the_resolver_answers() {
        let lo = Ipv4Addr::LOCALHOST;
        let (port, _h) = fake_resolver(0);
        assert!(resolver_answers(lo, &[lo], port, Duration::from_millis(800), 2));
        let (port, _h) = fake_resolver(1);
        let t = Instant::now();
        assert!(!resolver_answers(lo, &[lo], port, Duration::from_millis(300), 2), "a silent server is dead");
        assert!(t.elapsed() < Duration::from_secs(2), "and the probe gives up on time");
        let (port, _h) = fake_resolver(2);
        assert!(!resolver_answers(lo, &[lo], port, Duration::from_millis(300), 2), "a reply to some other question proves nothing");
        assert!(!resolver_answers(lo, &[], port, Duration::from_millis(300), 2), "no resolver, no probe");
        // an address this PC doesn't have (the tunnel is gone): nothing is sent at all
        assert!(!resolver_answers(Ipv4Addr::new(192, 0, 2, 77), &[lo], port, Duration::from_millis(300), 1));
    }

    #[test]
    fn median_ignores_nothing_it_shouldnt() {
        assert_eq!(median(vec![30.0, 10.0, 20.0]), Some(20.0));
        assert_eq!(median(vec![10.0, 20.0, 30.0, 40.0]), Some(25.0));
        assert_eq!(median(vec![]), None);
    }

    #[test]
    fn expected_dns_follows_the_config() {
        assert_eq!(expected_dns(&["10.66.0.1".into()], Some("178.156.252.184")), vec!["178.156.252.184"]);
        assert_eq!(expected_dns(&["1.1.1.1".into(), "1.0.0.1".into()], Some("2.29.149.29")), vec!["cloudflare"]);
        assert_eq!(expected_dns(&["9.9.9.9".into(), "149.112.112.112".into()], None), vec!["quad9"]);
    }
}
