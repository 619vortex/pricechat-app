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
}

pub struct Http {
    agent: ureq::Agent,
    base: String,
}

impl Http {
    pub fn new(base: &str) -> Result<Self, String> {
        let tls = native_tls::TlsConnector::new().map_err(|e| format!("Couldn't start a secure connection: {e}"))?;
        let agent = ureq::AgentBuilder::new().tls_connector(Arc::new(tls)).timeout_connect(Duration::from_secs(10)).timeout(Duration::from_secs(30)).redirects(0).build();
        Ok(Http { agent, base: base.trim_end_matches('/').to_string() })
    }

    fn url(&self, path: &str) -> String {
        format!("{}/api{path}", self.base)
    }

    fn with_session(&self, method: &str, path: &str, token: &str) -> ureq::Request {
        self.agent.request(method, &self.url(path)).set("Cookie", &format!("pc_session={token}")).set("Accept", "application/json")
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
}
