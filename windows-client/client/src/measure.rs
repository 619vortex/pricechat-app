//! Kiwi VPN 2.0's measurements, on request only: server timing for Smart Connect, the speed test and the leak check.
//! They run in the app (as the signed-in person, not SYSTEM) and send nothing about the person: no account, no device, no key.

use serde::Serialize;
use std::io::Read;
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

/// IPv4 and IPv6 as sites see them, and the DNS resolvers that answer this PC's lookups (ip-api's EDNS test: a random
/// name is looked up, and their server reports which resolver asked for it). Plain HTTP for that one test only: it carries no data.
pub fn leak_check(server_ip: Option<String>, config_dns: &[String]) -> Result<LeakReport, String> {
    let a = agent(Duration::from_secs(8))?;
    let text = |url: &str| a.get(url).call().ok().and_then(|r| r.into_string().ok()).map(|s| s.trim().to_string()).filter(|s| !s.is_empty() && s.len() < 64);
    let ipv4 = text("https://api.ipify.org");
    let ipv6 = text("https://api6.ipify.org"); // reachable only if IPv6 leaves the PC
    let mut dns = Vec::new();
    for _ in 0..3 {
        let mut r = [0u8; 8];
        getrandom::getrandom(&mut r).map_err(|e| e.to_string())?;
        let label: String = r.iter().map(|b| format!("{b:02x}")).collect();
        if let Ok(reply) = a.get(&format!("http://{label}.edns.ip-api.com/json")).call() {
            if let Ok(v) = reply.into_json::<serde_json::Value>() {
                let ip = v["dns"]["ip"].as_str().unwrap_or("").to_string();
                let geo = v["dns"]["geo"].as_str().unwrap_or("").to_string();
                let seen = if geo.is_empty() { ip } else { format!("{ip} ({geo})") };
                if !seen.is_empty() && !dns.contains(&seen) {
                    dns.push(seen);
                }
            }
        }
    }
    let expected_dns = expected_dns(config_dns, server_ip.as_deref());
    Ok(LeakReport { ipv4, ipv6, dns, tunnel_ip: server_ip, expected_dns })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_plain_host_names_are_timed() {
        assert!(valid_host("ash1.vpn.pricechat.live"));
        for bad in ["", "localhost", "evil.example/path", "a.b@c.d", "x.y:8443", "ash1.vpn.pricechat.live?x"] {
            assert!(!valid_host(bad), "{bad}");
        }
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
