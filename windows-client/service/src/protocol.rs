//! What the app may ask the service: exactly these three things, one JSON line each way. No paths, no commands.

use serde::{Deserialize, Serialize};

pub const PIPE_NAME: &str = r"\\.\pipe\KiwiVPN";
pub const MAX_REQUEST: usize = 16 * 1024;
/// WireGuard rekeys about every two minutes, so a tunnel that's working has handshaked within this.
pub const HANDSHAKE_FRESH_SECONDS: u64 = 180;

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "cmd", rename_all = "snake_case", deny_unknown_fields)]
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
}

impl Status {
    pub fn off() -> Self {
        Status { state: State::Disconnected, handshake_age_seconds: None, rx_bytes: 0, tx_bytes: 0, error: None }
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
    Status { state, handshake_age_seconds: handshake_age, rx_bytes: rx, tx_bytes: tx, error: None }
}

pub fn parse_request(line: &str) -> Result<Request, String> {
    if line.len() > MAX_REQUEST {
        return Err("request too large".into());
    }
    serde_json::from_str(line).map_err(|_| "request not understood".to_string())
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
