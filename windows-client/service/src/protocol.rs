//! What the app may ask the service: exactly these six things, one JSON line each way. No paths, no URLs, no commands.

pub use crate::guard::Protect;
use serde::{Deserialize, Serialize};

pub const PIPE_NAME: &str = r"\\.\pipe\KiwiVPN";
pub const MAX_REQUEST: usize = 16 * 1024;
/// WireGuard rekeys about every two minutes, so a tunnel that's working has handshaked within this.
pub const HANDSHAKE_FRESH_SECONDS: u64 = 180;

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum Request {
    Connect {
        config: String,
        /// The kill switch and what it lets through. Absent from an older app: then only DNS outside the tunnel is blocked.
        #[serde(default)]
        protect: Protect,
    },
    Disconnect,
    /// Close the tunnel but keep the kill switch blocking, for a moment: a server that stopped answering still has the PC's routes, so the
    /// app can't fetch the next server's settings until it's closed. A connect within PAUSE_LIMIT_SECONDS carries on; otherwise the block goes.
    Pause,
    Status,
    /// 2.2: look for an update now (the service fetches its one manifest; at most once per UPDATE_CHECK_EVERY_SECONDS). The answer's
    /// `update` says what it found.
    UpdateCheck,
    /// 2.2: install the update the service already found and verified. Carries nothing: no URL, path or file.
    UpdateInstall,
}

/// The app's own asking is limited to this; the service checks by itself every few hours anyway.
pub const UPDATE_CHECK_EVERY_SECONDS: u64 = 600;

/// How long a pause may hold the kill switch without a connect following it.
pub const PAUSE_LIMIT_SECONDS: u64 = 120;

impl Request {
    pub fn connect(config: impl Into<String>) -> Self {
        Request::Connect { config: config.into(), protect: Protect::default() }
    }
}

/// What this service enforces, so the app offers only what's real.
pub const FEATURES: &[&str] = &["kill_switch", "dns_guard", "strict_kill_switch", "dot_guard", "update"];

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
    /// True while the kill switch is blocking everything outside the VPN.
    #[serde(default)]
    pub kill_switch: bool,
    /// What this service can do (see FEATURES). Empty from an older service.
    #[serde(default)]
    pub features: Vec<String>,
    /// 2.2: the strict kill switch's persistent filters are in place. They stay until Disconnect or `kiwivpn-service unblock`.
    #[serde(default)]
    pub strict: bool,
    /// 2.2: what the service knows about updates. Absent from an older service.
    #[serde(default)]
    pub update: Option<crate::update::UpdateInfo>,
    /// 2.2: the service's own version, so the recovery task can tell whether an update finished.
    #[serde(default)]
    pub version: Option<String>,
}

impl Status {
    pub fn off() -> Self {
        Status { state: State::Disconnected, handshake_age_seconds: None, rx_bytes: 0, tx_bytes: 0, error: None, connected_since: None, kill_switch: false, features: Vec::new(), strict: false, update: None, version: None }
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
    Status { state, handshake_age_seconds: handshake_age, rx_bytes: rx, tx_bytes: tx, error: None, connected_since: None, kill_switch: false, features: Vec::new(), strict: false, update: None, version: None }
}

/// Someone wants the tunnel, nothing is changing it, and it isn't running: it stopped by itself, such as failing just after starting. Without this it would read as an ordinary "not connected",
/// and pressing Connect would seem to do nothing.
pub fn tunnel_ended(service: ServiceState, wanted: bool, changing: bool) -> bool {
    service == ServiceState::Stopped && wanted && !changing
}

/// The message for a tunnel that stopped by itself. Windows' exit code, when there is one, is what points to the reason.
pub fn ended_message(exit_code: u32) -> String {
    const NO_CODE: [u32; 2] = [0, 1077]; // ran and stopped normally, or never started
    if NO_CODE.contains(&exit_code) {
        "The connection stopped unexpectedly. Please try again.".into()
    } else {
        format!("The connection stopped unexpectedly (Windows error {exit_code}). Please try again.")
    }
}

/// How long a new connection may wait for the server's first answer before it's given up on, and everything it set up is taken down.
pub const CONNECT_LIMIT_SECONDS: u64 = 60;
pub const NO_ANSWER: &str = "The server didn't answer, so the connection was closed. Please try again, or choose another country.";

/// A connection this person just asked for that has never reached the server. A tunnel that had reached it and lost it is different, and keeps trying: that's a network interruption,
/// and it recovers by itself. `started` is unset for a tunnel brought back after a restart, which may be waiting for the network to come up.
pub fn connect_timed_out(started: Option<u64>, status: &Status, now: u64) -> bool {
    status.state == State::Connecting && status.connected_since.is_none() && started.is_some_and(|at| now.saturating_sub(at) >= CONNECT_LIMIT_SECONDS)
}

/// The session's start, given what was known before and the latest reading. It's set by the first handshake seen, survives a handshake running late, and ends with the tunnel.
pub fn session_since(previous: Option<u64>, status: &Status, now: u64) -> Option<u64> {
    match status.state {
        State::Connected => previous.or_else(|| Some(now.saturating_sub(status.handshake_age_seconds.unwrap_or(0)))),
        State::Connecting | State::Stopping => previous,
        State::Disconnected | State::Failed => None,
    }
}

/// A tunnel that stopped by itself while the kill switch holds is started again, this many times, this far apart. If it still won't run,
/// the block is released: being offline indefinitely is worse than the leak the person can now see and act on.
pub const RESTART_TRIES: u32 = 6;
pub const RESTART_EVERY_SECONDS: u64 = 15;
/// After Windows restarts, a tunnel brought back has this long to reach its server before the kill switch lets go.
pub const RESUME_LIMIT_SECONDS: u64 = 600;
pub const RELEASED: &str = "The VPN couldn't reconnect, so the kill switch was turned off to keep you online. Connect again when you're ready.";
/// The strict kill switch never lets go by itself (audit M3): the same give-up closes the tunnel, and says so.
pub const STRICT_HELD: &str = "The VPN couldn't reconnect. The strict kill switch is still blocking all internet traffic: connect again, or press Disconnect to get your normal connection back.";

/// The cases where the normal kill switch lets traffic out unprotected, so the app and the docs can list them (audit M3). The strict
/// kill switch holds through all of them except a reinstall or uninstall; only Disconnect, `kiwivpn-service unblock` or uninstalling
/// removes it. Keep in step with the app's settings text (app/src/tools.tsx RELEASE_CASES).
pub const RELEASE_CASES: &[&str] = &[
    "for about 3 seconds while Windows restarts the Kiwi VPN service after a crash",
    "while Windows starts, before the Kiwi VPN service is running",
    "after the tunnel fails to restart 6 times in a row",
    "10 minutes after a restart if the server never answers",
    "2 minutes after a server switch that never completes",
    "while Kiwi VPN is being updated",
];

/// Whether the service should give up bringing the tunnel back and release the block.
pub fn recovery_given_up(restarts: u32, resumed_at: Option<u64>, ever_connected: bool, now: u64) -> bool {
    restarts >= RESTART_TRIES || resumed_at.is_some_and(|at| !ever_connected && now.saturating_sub(at) >= RESUME_LIMIT_SECONDS)
}

/// Who's on the other end of the pipe, as the service worked it out from Windows, never from anything the client said.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Caller {
    /// KiwiVPN.exe from the service's own install folder, which only administrators can write to.
    App,
    /// Any program with administrator rights. It could replace the service anyway, so refusing it would protect nothing.
    Elevated,
    Other,
}

pub const NOT_ALLOWED: &str = "Only the Kiwi VPN app can do that.";

/// Anyone signed in may read the status. Changing the tunnel is for the app.
pub fn allowed(request: &Request, caller: Caller) -> bool {
    matches!(request, Request::Status) || caller != Caller::Other
}

/// `image` is the client's full path from Windows; `install_dir` the service's own folder.
pub fn is_app_image(image: &str, install_dir: &str, app_exe: &str) -> bool {
    image.eq_ignore_ascii_case(&format!("{}\\{}", install_dir.trim_end_matches('\\'), app_exe))
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
        Some("connect") => &["cmd", "config", "protect"],
        Some("disconnect" | "pause" | "status" | "update_check" | "update_install") => &["cmd"],
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
    fn a_new_connection_gives_up_and_an_established_one_keeps_trying() {
        let waiting = decide(ServiceState::Running, None, 0, 0);
        assert!(!connect_timed_out(Some(1000), &waiting, 1000 + CONNECT_LIMIT_SECONDS - 1));
        assert!(connect_timed_out(Some(1000), &waiting, 1000 + CONNECT_LIMIT_SECONDS));
        assert!(!connect_timed_out(None, &waiting, 9999), "brought back after a restart: the network may still be coming up");
        let interrupted = Status { connected_since: Some(1005), ..decide(ServiceState::Running, Some(500), 0, 0) };
        assert_eq!(interrupted.state, State::Connecting);
        assert!(!connect_timed_out(Some(1000), &interrupted, 9999), "it reached the server before, so it's a network interruption");
        assert!(!connect_timed_out(Some(1000), &decide(ServiceState::Running, Some(1), 0, 0), 9999));
        assert!(!connect_timed_out(Some(1000), &Status::off(), 9999));
    }

    #[test]
    fn a_tunnel_that_stops_by_itself_is_a_failure_and_a_disconnect_is_not() {
        assert!(tunnel_ended(ServiceState::Stopped, true, false), "wanted, and nothing is changing it");
        assert!(!tunnel_ended(ServiceState::Stopped, false, false), "the state after Disconnect, and before any Connect");
        assert!(!tunnel_ended(ServiceState::Stopped, true, true), "a connect is starting it, or the service is retrying after a restart");
        for state in [ServiceState::Running, ServiceState::Starting, ServiceState::Stopping, ServiceState::Missing] {
            assert!(!tunnel_ended(state, true, false), "{state:?}");
        }
        assert!(ended_message(1168).contains("Windows error 1168"));
        assert!(!ended_message(0).contains("Windows error") && !ended_message(1077).contains("Windows error"));
    }

    #[test]
    fn only_the_app_or_an_administrator_may_change_the_tunnel() {
        let connect = Request::connect("x");
        for request in [&connect, &Request::Disconnect] {
            assert!(allowed(request, Caller::App));
            assert!(allowed(request, Caller::Elevated));
            assert!(!allowed(request, Caller::Other));
        }
        for caller in [Caller::App, Caller::Elevated, Caller::Other] {
            assert!(allowed(&Request::Status, caller));
        }
    }

    #[test]
    fn the_app_is_a_full_path_not_a_name() {
        let dir = r"C:\Program Files\KiwiVPN";
        assert!(is_app_image(r"C:\Program Files\KiwiVPN\KiwiVPN.exe", dir, "KiwiVPN.exe"));
        assert!(is_app_image(r"c:\program files\kiwivpn\kiwivpn.EXE", dir, "KiwiVPN.exe"));
        assert!(!is_app_image(r"C:\Users\me\Downloads\KiwiVPN.exe", dir, "KiwiVPN.exe"));
        assert!(!is_app_image(r"C:\Program Files\KiwiVPN\sub\KiwiVPN.exe", dir, "KiwiVPN.exe"));
        assert!(!is_app_image(r"C:\Program Files\KiwiVPN\kiwivpn-service.exe", dir, "KiwiVPN.exe"));
        assert!(!is_app_image(r"C:\Program Files\KiwiVPN2\KiwiVPN.exe", dir, "KiwiVPN.exe"));
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
    fn the_kill_switch_lets_go_only_when_the_tunnel_can_t_be_brought_back() {
        assert!(!recovery_given_up(RESTART_TRIES - 1, None, true, 0));
        assert!(recovery_given_up(RESTART_TRIES, None, true, 0));
        assert!(!recovery_given_up(0, Some(1000), false, 1000 + RESUME_LIMIT_SECONDS - 1));
        assert!(recovery_given_up(0, Some(1000), false, 1000 + RESUME_LIMIT_SECONDS));
        assert!(!recovery_given_up(0, Some(1000), true, 99_999), "it reached the server after the restart: an ordinary interruption from then on");
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
        assert_eq!(parse_request(r#"{"cmd":"pause"}"#).unwrap(), Request::Pause);
        assert!(!allowed(&Request::Pause, Caller::Other), "only the app may pause the tunnel");
        assert_eq!(parse_request(r#"{"cmd":"connect","config":"x"}"#).unwrap(), Request::connect("x"), "an older app's connect still reads");
        let protected = parse_request(r#"{"cmd":"connect","config":"x","protect":{"kill_switch":true,"allow_lan":true,"allow":["192.168.1.0/24"]}}"#).unwrap();
        assert!(matches!(protected, Request::Connect { protect: Protect { kill_switch: true, allow_lan: true, .. }, .. }));
        assert!(parse_request(r#"{"cmd":"connect","config":"x","protect":{"kill_switch":true,"exec":"calc"}}"#).is_err());
        for bad in [r#"{"cmd":"run","path":"calc.exe"}"#, r#"{"cmd":"status","path":"x"}"#, r#"{"cmd":"connect"}"#, "not json", ""] {
            assert!(parse_request(bad).is_err(), "{bad}");
        }
        assert!(parse_request(&"x".repeat(MAX_REQUEST + 1)).is_err());
    }

    #[test]
    fn update_requests_carry_nothing_and_only_the_app_may_send_them() {
        assert_eq!(parse_request(r#"{"cmd":"update_check"}"#).unwrap(), Request::UpdateCheck);
        assert_eq!(parse_request(r#"{"cmd":"update_install"}"#).unwrap(), Request::UpdateInstall);
        for bad in [r#"{"cmd":"update_install","url":"https://evil.example/x.exe"}"#, r#"{"cmd":"update_install","path":"C:\\x.exe"}"#, r#"{"cmd":"update_check","host":"x"}"#] {
            assert!(parse_request(bad).is_err(), "{bad}");
        }
        for r in [Request::UpdateCheck, Request::UpdateInstall] {
            assert!(!allowed(&r, Caller::Other), "{r:?}");
            assert!(allowed(&r, Caller::App) && allowed(&r, Caller::Elevated));
        }
        assert!(FEATURES.contains(&"update") && FEATURES.contains(&"strict_kill_switch") && FEATURES.contains(&"dot_guard"));
    }

    #[test]
    fn a_2_1_answer_reads_with_no_update_and_no_strict_block() {
        let s: Status = serde_json::from_str(r#"{"state":"disconnected","handshake_age_seconds":null,"rx_bytes":0,"tx_bytes":0,"error":null,"kill_switch":true,"features":["kill_switch"]}"#).unwrap();
        assert!(!s.strict && s.update.is_none() && s.kill_switch);
        assert_eq!(RELEASE_CASES.len(), 6);
    }
}
