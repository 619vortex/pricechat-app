//! What the app may ask the service: exactly these three things, one JSON line each way. No paths, no commands.

use serde::{Deserialize, Serialize};

pub const PIPE_NAME: &str = r"\\.\pipe\KiwiVPN";
pub const MAX_REQUEST: usize = 16 * 1024;
/// WireGuard rekeys about every two minutes, so a tunnel that's working has handshaked within this.
pub const HANDSHAKE_FRESH_SECONDS: u64 = 180;

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum Request {
    Connect { config: String },
    Disconnect,
    Status,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Clone, Copy)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Disconnected,
    /// The tunnel is up but hasn't reached the server yet. Never shown as connected.
    Connecting,
    Connected,
    Stopping,
    Failed,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Clone)]
pub struct Status {
    pub state: State,
    pub handshake_age_seconds: Option<u64>,
    pub rx_bytes: u64,
    pub tx_bytes: u64,
    pub error: Option<String>,
    /// When this tunnel first reached the server, in Unix seconds. Kept by the service, so it outlives the app's window.
    #[serde(default)]
    pub connected_since: Option<u64>,
}

impl Status {
    pub fn off() -> Self {
        Status { state: State::Disconnected, handshake_age_seconds: None, rx_bytes: 0, tx_bytes: 0, error: None, connected_since: None }
    }
    pub fn failed(error: impl Into<String>) -> Self {
        Status { state: State::Failed, error: Some(error.into()), ..Status::off() }
    }
}

/// The tunnel's state as far as the operating system knows it, before we ask whether it's reached the server.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ServiceState {
    Missing,
    Starting,
    Running,
    Stopping,
    Stopped,
}

/// Connected only when the service runs and the server has answered recently. This is the one rule behind the word "Connected".
pub fn decide(service: ServiceState, handshake_age: Option<u64>, rx: u64, tx: u64) -> Status {
    let state = match service {
        ServiceState::Missing | ServiceState::Stopped => State::Disconnected,
        ServiceState::Stopping => State::Stopping,
        ServiceState::Starting => State::Connecting,
        ServiceState::Running => match handshake_age {
            Some(age) if age <= HANDSHAKE_FRESH_SECONDS => State::Connected,
            _ => State::Connecting,
        },
    };
    Status { state, handshake_age_seconds: handshake_age, rx_bytes: rx, tx_bytes: tx, error: None, connected_since: None }
}

/// The session's start, given what was known before and the latest reading. It's set by the first handshake seen, survives a handshake running late, and ends with the tunnel.
pub fn session_since(previous: Option<u64>, status: &Status, now: u64) -> Option<u64> {
    match status.state {
        State::Connected => previous.or_else(|| Some(now.saturating_sub(status.handshake_age_seconds.unwrap_or(0)))),
        State::Connecting | State::Stopping => previous,
        State::Disconnected | State::Failed => None,
    }
}

pub fn parse_request(line: &str) -> Result<Request, String> {
    if line.len() > MAX_REQUEST {
        return Err("request too large".into());
    }
    let bad = || "request not understood".to_string();
    let value: serde_json::Value = serde_json::from_str(line).map_err(|_| bad())?;
    let fields = value.as_object().ok_or_else(bad)?;
    // serde lets extra fields through on a tagged enum, so they're refused here: nothing unexpected reaches a service running as SYSTEM
    let allowed: &[&str] = match fields.get("cmd").and_then(|c| c.as_str()) {
        Some("connect") => &["cmd", "config"],
        Some("disconnect" | "status") => &["cmd"],
        _ => return Err(bad()),
    };
    if fields.keys().any(|k| !allowed.contains(&k.as_str())) {
        return Err(bad());
    }
    serde_json::from_value(value).map_err(|_| bad())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connected_needs_a_running_tunnel_and_a_recent_handshake() {
        assert_eq!(decide(ServiceState::Running, Some(5), 1, 1).state, State::Connected);
        assert_eq!(decide(ServiceState::Running, Some(HANDSHAKE_FRESH_SECONDS), 0, 0).state, State::Connected);
        assert_eq!(decide(ServiceState::Running, Some(HANDSHAKE_FRESH_SECONDS + 1), 0, 0).state, State::Connecting);
        assert_eq!(decide(ServiceState::Running, None, 0, 0).state, State::Connecting);
        assert_eq!(decide(ServiceState::Starting, Some(1), 0, 0).state, State::Connecting);
        assert_eq!(decide(ServiceState::Stopping, Some(1), 0, 0).state, State::Stopping);
        assert_eq!(decide(ServiceState::Stopped, Some(1), 0, 0).state, State::Disconnected);
        assert_eq!(decide(ServiceState::Missing, None, 0, 0).state, State::Disconnected);
    }

    #[test]
    fn a_session_starts_at_the_first_handshake_and_ends_with_the_tunnel() {
        let at = |service, age| decide(service, age, 0, 0);
        assert_eq!(session_since(None, &at(ServiceState::Running, None), 1000), None, "no handshake, no session");
        assert_eq!(session_since(None, &at(ServiceState::Running, Some(4)), 1000), Some(996));
        assert_eq!(session_since(Some(996), &at(ServiceState::Running, Some(1)), 5000), Some(996), "a rekey doesn't restart it");
        assert_eq!(session_since(Some(996), &at(ServiceState::Running, Some(500)), 5000), Some(996), "nor does a late handshake");
        assert_eq!(session_since(Some(996), &at(ServiceState::Stopped, None), 5000), None);
        assert_eq!(session_since(Some(996), &Status::failed("x"), 5000), None);
    }

    #[test]
    fn an_answer_from_an_older_service_still_reads() {
        let s: Status = serde_json::from_str(r#"{"state":"connected","handshake_age_seconds":3,"rx_bytes":1,"tx_bytes":2,"error":null}"#).unwrap();
        assert_eq!(s.connected_since, None);
    }

    #[test]
    fn only_the_three_commands_exist() {
        assert_eq!(parse_request(r#"{"cmd":"status"}"#).unwrap(), Request::Status);
        assert_eq!(parse_request(r#"{"cmd":"disconnect"}"#).unwrap(), Request::Disconnect);
        assert_eq!(parse_request(r#"{"cmd":"connect","config":"x"}"#).unwrap(), Request::Connect { config: "x".into() });
        for bad in [r#"{"cmd":"run","path":"calc.exe"}"#, r#"{"cmd":"status","path":"x"}"#, r#"{"cmd":"connect"}"#, "not json", ""] {
            assert!(parse_request(bad).is_err(), "{bad}");
        }
        assert!(parse_request(&"x".repeat(MAX_REQUEST + 1)).is_err());
    }
}
