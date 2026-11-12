//! Calls to Price Chat, from Rust rather than the web page: the session token never enters the page, and the backend refuses cross-site POSTs, which a page's would be.

use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::Duration;

pub const BASE: &str = "https://pricechat.live";
pub const UNREACHABLE: &str = "Couldn't reach Price Chat. Check your connection and try again.";

#[derive(Debug, Clone, PartialEq)]
pub struct ApiError {
    pub status: u16,
    pub message: String,
}

impl ApiError {
    pub fn signed_out(&self) -> bool {
        self.status == 401
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Server {
    pub id: i64,
    pub name: String,
    #[serde(default)]
    pub region: String,
    #[serde(default)]
    pub country_code: String,
    pub online: bool,
    #[serde(default)]
    pub recommended: bool,
    // 2.0, sent by newer backends; absent means unknown
    #[serde(default)]
    pub load: Option<f64>,
    #[serde(default)]
    pub live_load: Option<f64>,
    #[serde(default)]
    pub adblock: bool,
    #[serde(default)]
    pub proxy_host: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Device {
    pub id: i64,
    pub name: String,
    pub server_id: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct Plan {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub days_left: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct Trial {
    #[serde(default)]
    pub available: bool,
    #[serde(default)]
    pub days: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Account {
    pub enabled: bool,
    pub eligible: bool,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub max_devices: i64,
    #[serde(default)]
    pub servers: Vec<Server>,
    #[serde(default)]
    pub devices: Vec<Device>,
    #[serde(default)]
    pub plan: Option<Plan>,
    #[serde(default)]
    pub trial: Option<Trial>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Peer {
    pub server_public_key: String,
    pub endpoint_host: String,
    pub listen_port: u16,
    pub addresses: Vec<String>,
    #[serde(default)]
    pub dns: Vec<String>,
    pub allowed_ips: Vec<String>,
    #[serde(default)]
    pub keepalive: u16,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NewDevice {
    pub device: Device,
    pub peer: Peer,
}

/// What the flow needs from Price Chat, so tests can stand in for it.
pub trait Backend {
    fn account(&self, token: &str) -> Result<Account, ApiError>;
    fn start_trial(&self, token: &str) -> Result<Account, ApiError>;
    fn peer(&self, token: &str, device_id: i64) -> Result<Peer, ApiError>;
    fn add_device(&self, token: &str, name: &str, server_id: i64, public_key: &str) -> Result<NewDevice, ApiError>;
    fn delete_device(&self, token: &str, device_id: i64) -> Result<(), ApiError>;
    fn token(&self, code: &str, verifier: &str) -> Result<String, ApiError>;
    fn logout(&self, token: &str) -> Result<(), ApiError>;
    fn refresh(&self, token: &str) -> Result<String, ApiError>;
    fn my_ip(&self) -> Result<String, ApiError>;
    /// Tools › Support (2.1): a bug report or feedback. `diagnostics` is already stripped (see `support::report`).
    fn support(&self, token: &str, kind: &str, message: &str, app_version: &str, diagnostics: Option<&str>) -> Result<(), ApiError>;
}

pub const SUPPORT_PATH: &str = "/members/vpn/support";

pub struct Http {
    agent: ureq::Agent,
    base: String,
}

impl Http {
    pub fn new(base: &str) -> Result<Self, String> {
        let tls = native_tls::TlsConnector::new().map_err(|e| format!("Couldn't start a secure connection: {e}"))?;
        let tls = Arc::new(tls);
        let resolver = Fallback { tls: Arc::clone(&tls) };
        let agent = ureq::AgentBuilder::new().tls_connector(tls).resolver(resolver).timeout_connect(Duration::from_secs(10)).timeout(Duration::from_secs(30)).redirects(0).build();
        Ok(Http { agent, base: base.trim_end_matches('/').to_string() })
    }

    fn url(&self, path: &str) -> String {
        format!("{}/api{path}", self.base)
    }

    fn with_session(&self, method: &str, path: &str, token: &str) -> ureq::Request {
        self.agent.request(method, &self.url(path)).set("Cookie", &format!("pc_session={token}")).set("Accept", "application/json")
    }
}

/// Windows' resolver first. If it can't answer, which is what happens while the kill switch blocks DNS outside the VPN and the app needs a
/// new server's settings, Price Chat's own names are looked up over HTTPS from Cloudflare by address. The kill switch lets Kiwi VPN's
/// own programs through, so this still works then; nothing else on the PC can use it.
struct Fallback {
    tls: Arc<native_tls::TlsConnector>,
}

/// Cloudflare's resolver, reached by address (its certificate names these addresses).
const DOH: &[&str] = &["https://1.1.1.1/dns-query", "https://1.0.0.1/dns-query"];

pub fn ours(host: &str) -> bool {
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    host == "pricechat.live" || host.ends_with(".pricechat.live")
}

/// The IPv4 addresses in a DNS-over-HTTPS JSON answer.
pub fn doh_addresses(answer: &serde_json::Value) -> Vec<std::net::IpAddr> {
    answer.get("Answer").and_then(|a| a.as_array()).into_iter().flatten()
        .filter(|r| r.get("type").and_then(|t| t.as_u64()) == Some(1))
        .filter_map(|r| r.get("data").and_then(|d| d.as_str()).and_then(|d| d.parse::<std::net::Ipv4Addr>().ok()).map(std::net::IpAddr::V4))
        .collect()
}

impl ureq::Resolver for Fallback {
    fn resolve(&self, netloc: &str) -> std::io::Result<Vec<std::net::SocketAddr>> {
        use std::net::ToSocketAddrs;
        let system = netloc.to_socket_addrs().map(|a| a.collect::<Vec<_>>());
        if let Ok(found) = &system {
            if !found.is_empty() {
                return Ok(found.clone());
            }
        }
        let (host, port) = netloc.rsplit_once(':').and_then(|(h, p)| p.parse::<u16>().ok().map(|p| (h, p))).ok_or_else(|| std::io::Error::other("no port"))?;
        if !ours(host) {
            return system;
        }
        let agent = ureq::AgentBuilder::new().tls_connector(Arc::clone(&self.tls)).timeout(Duration::from_secs(6)).redirects(0).build();
        for url in DOH {
            if let Ok(reply) = agent.get(url).query("name", host).query("type", "A").set("Accept", "application/dns-json").call() {
                if let Ok(v) = reply.into_json::<serde_json::Value>() {
                    let found: Vec<_> = doh_addresses(&v).into_iter().map(|ip| std::net::SocketAddr::new(ip, port)).collect();
                    if !found.is_empty() {
                        return Ok(found);
                    }
                }
            }
        }
        system
    }
}

fn failure(e: ureq::Error) -> ApiError {
    match e {
        ureq::Error::Status(status, reply) => {
            let message = reply.into_json::<serde_json::Value>().ok().and_then(|v| v.get("detail").and_then(|d| d.as_str()).map(str::to_string)).unwrap_or_else(|| format!("Price Chat answered {status}."));
            ApiError { status, message }
        }
        ureq::Error::Transport(_) => ApiError { status: 0, message: UNREACHABLE.into() },
    }
}

fn parsed<T: serde::de::DeserializeOwned>(reply: Result<ureq::Response, ureq::Error>) -> Result<T, ApiError> {
    reply.map_err(failure)?.into_json::<T>().map_err(|_| ApiError { status: 0, message: "Price Chat gave an answer the app can't read.".into() })
}

impl Backend for Http {
    fn account(&self, token: &str) -> Result<Account, ApiError> {
        parsed(self.with_session("GET", "/members/vpn", token).call())
    }

    fn start_trial(&self, token: &str) -> Result<Account, ApiError> {
        parsed(self.with_session("POST", "/members/vpn/trial", token).call())
    }

    fn peer(&self, token: &str, device_id: i64) -> Result<Peer, ApiError> {
        parsed(self.with_session("GET", &format!("/members/vpn/devices/{device_id}/peer"), token).call())
    }

    fn add_device(&self, token: &str, name: &str, server_id: i64, public_key: &str) -> Result<NewDevice, ApiError> {
        parsed(self.with_session("POST", "/members/vpn/devices", token).send_json(serde_json::json!({ "name": name, "server_id": server_id, "public_key": public_key })))
    }

    fn delete_device(&self, token: &str, device_id: i64) -> Result<(), ApiError> {
        self.with_session("DELETE", &format!("/members/vpn/devices/{device_id}"), token).call().map(|_| ()).map_err(failure)
    }

    fn token(&self, code: &str, verifier: &str) -> Result<String, ApiError> {
        #[derive(Deserialize)]
        struct Reply {
            token: String,
        }
        let reply: Reply = parsed(self.agent.post(&self.url("/members/desktop/token")).send_json(serde_json::json!({ "code": code, "verifier": verifier })))?;
        Ok(reply.token)
    }

    fn logout(&self, token: &str) -> Result<(), ApiError> {
        self.with_session("POST", "/members/logout", token).call().map(|_| ()).map_err(failure)
    }

    fn refresh(&self, token: &str) -> Result<String, ApiError> {
        #[derive(Deserialize)]
        struct Reply {
            token: String,
        }
        let reply: Reply = parsed(self.with_session("POST", "/members/desktop/refresh", token).call())?;
        Ok(reply.token)
    }

    fn my_ip(&self) -> Result<String, ApiError> {
        #[derive(Deserialize)]
        struct Reply {
            ip: String,
        }
        let reply: Reply = parsed(self.agent.get(&self.url("/members/vpn/ip")).call())?;
        Ok(reply.ip)
    }

    fn support(&self, token: &str, kind: &str, message: &str, app_version: &str, diagnostics: Option<&str>) -> Result<(), ApiError> {
        let body = crate::support::body(kind, message, app_version, diagnostics);
        self.with_session("POST", SUPPORT_PATH, token).send_json(body).map(|_| ()).map_err(failure)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_price_chat_s_names_use_the_https_lookup() {
        for yes in ["pricechat.live", "PRICECHAT.LIVE", "hel1.vpn.pricechat.live", "pricechat.live."] {
            assert!(ours(yes), "{yes}");
        }
        for no in ["evilpricechat.live", "pricechat.live.evil.com", "google.com", ""] {
            assert!(!ours(no), "{no}");
        }
    }

    #[test]
    fn a_dns_over_https_answer_gives_its_ipv4_addresses_only() {
        let v = serde_json::json!({"Status":0,"Answer":[{"name":"pricechat.live","type":5,"data":"x.example."},{"name":"pricechat.live","type":1,"data":"76.76.21.21"},{"type":1,"data":"not an ip"},{"type":28,"data":"2606::1"}]});
        assert_eq!(doh_addresses(&v), vec!["76.76.21.21".parse::<std::net::IpAddr>().unwrap()]);
        assert!(doh_addresses(&serde_json::json!({"Status":3})).is_empty());
    }

    /// One request to a local stand-in for Price Chat: what it received (request head, body), after answering `status` with `reply`.
    fn one_request(status: &str, reply: &'static str, call: impl FnOnce(&Http)) -> (String, String) {
        use std::io::{BufRead, BufReader, Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let status = status.to_string();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut head = String::new();
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                if line == "\r\n" || line.is_empty() {
                    break;
                }
                head.push_str(&line);
            }
            let length = head.lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap())).expect("a Content-Length");
            let mut body = vec![0u8; length];
            reader.read_exact(&mut body).unwrap();
            write!(stream, "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}", reply.len()).unwrap();
            stream.flush().unwrap();
            (head, String::from_utf8(body).unwrap())
        });
        call(&Http::new(&format!("http://127.0.0.1:{port}")).unwrap());
        server.join().unwrap()
    }

    #[test]
    fn a_support_report_is_posted_with_the_session_cookie_and_the_expected_body() {
        let (head, body) = one_request("201 Created", r#"{"ok":true,"id":7}"#, |http| {
            http.support("kd.session", "bug", "  Failover didn't move me from 10.0.0.2  ", "2.1.0", Some("Kiwi VPN for Windows 2.1.0")).unwrap();
        });
        assert!(head.starts_with("POST /api/members/vpn/support HTTP/1.1\r\n"), "{head}");
        assert!(head.lines().any(|l| l == "Cookie: pc_session=kd.session"), "{head}");
        assert!(head.to_ascii_lowercase().contains("content-type: application/json"), "{head}");
        let sent: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(sent, serde_json::json!({ "kind": "bug", "message": "Failover didn't move me from [address]", "app_version": "2.1.0", "diagnostics": "Kiwi VPN for Windows 2.1.0" }));
    }

    #[test]
    fn a_support_report_without_diagnostics_leaves_them_out_and_a_refusal_keeps_price_chat_s_words() {
        let mut got = None;
        let (_, body) = one_request("429 Too Many Requests", r#"{"detail":"You've sent several reports already. Please try again later."}"#, |http| {
            got = Some(http.support("kd.session", "feedback", "Nice app", "2.1.0", None));
        });
        assert_eq!(serde_json::from_str::<serde_json::Value>(&body).unwrap(), serde_json::json!({ "kind": "feedback", "message": "Nice app", "app_version": "2.1.0" }));
        assert_eq!(got.unwrap().unwrap_err(), ApiError { status: 429, message: "You've sent several reports already. Please try again later.".into() });
    }
}
