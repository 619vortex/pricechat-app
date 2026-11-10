//! Tools › Support: a bug report or feedback sent to Price Chat (POST /api/members/vpn/support, see `api::Backend::support`).
//!
//! The diagnostics are put together here, in Rust, never by the page: the app's version, the service's state, which options are on
//! (on/off only, never the split-tunnel ranges themselves), and the last lines of the app's own log. Then `strip` removes anything that
//! could be secret or locate the person: WireGuard key lines and anything shaped like a WireGuard key, every IPv4 and IPv6 address (the
//! home network's, the public one, the tunnel's), and the Windows user name in paths. Price Chat redacts again on its side.

pub const KINDS: [&str; 2] = ["bug", "feedback"];
pub const MAX_MESSAGE: usize = 4000;
/// Below Price Chat's 20 000 character limit, so the server never has to cut it.
pub const MAX_DIAGNOSTICS: usize = 16_000;
/// How much of the end of the app's log goes with a report.
pub const LOG_LINES: usize = 60;
pub const REDACTED: &str = "[redacted]";
pub const ADDRESS: &str = "[address]";

/// Words that mark a whole line as secret, whatever else is on it (any case, with or without the separator).
const KEY_WORDS: [&str; 4] = ["privatekey", "presharedkey", "private_key", "preshared_key"];

/// The page's input, checked here too: the page can't send another kind, an empty message or a longer one.
pub fn check(kind: &str, message: &str) -> Result<(), String> {
    if !KINDS.contains(&kind) {
        return Err("Choose Bug or Feedback.".into());
    }
    let n = message.trim().chars().count();
    if n == 0 {
        return Err("Please write a message.".into());
    }
    if n > MAX_MESSAGE {
        return Err(format!("Please keep it under {MAX_MESSAGE} characters."));
    }
    Ok(())
}

/// The request body Price Chat expects. `diagnostics` is already stripped.
pub fn body(kind: &str, message: &str, app_version: &str, diagnostics: Option<&str>) -> serde_json::Value {
    let mut out = serde_json::json!({ "kind": kind, "message": strip(message.trim()), "app_version": app_version });
    if let Some(d) = diagnostics.filter(|d| !d.trim().is_empty()) {
        out["diagnostics"] = serde_json::Value::String(d.to_string());
    }
    out
}

/// What's known about this PC's Kiwi VPN when the report is sent. Plain facts; `report` turns them into text.
#[derive(Debug, Default, Clone)]
pub struct Facts {
    pub app_version: String,
    pub service: String,
    pub tunnel: String,
    pub handshake_age_seconds: Option<u64>,
    pub tunnel_error: Option<String>,
    pub kill_switch_active: bool,
    pub features: Vec<String>,
    /// (name, on) for each option: never the values of the split-tunnel ranges
    pub options: Vec<(String, bool)>,
    pub split_ranges: usize,
    pub signed_in: bool,
    pub last_server: Option<i64>,
    pub known_servers: Vec<i64>,
    /// The whole app log as read from disk; only its last LOG_LINES lines are used.
    pub log: String,
}

pub fn report(f: &Facts) -> String {
    let mut out = String::new();
    out.push_str(&format!("Kiwi VPN for Windows {}\n", f.app_version));
    out.push_str(&format!("Service: {}\nTunnel: {}\n", f.service, f.tunnel));
    out.push_str(&format!("Last handshake: {}\n", f.handshake_age_seconds.map(|s| format!("{s} s ago")).unwrap_or_else(|| "none".into())));
    if let Some(e) = &f.tunnel_error {
        out.push_str(&format!("Tunnel error: {e}\n"));
    }
    out.push_str(&format!("Kill switch blocking now: {}\n", if f.kill_switch_active { "yes" } else { "no" }));
    out.push_str(&format!("Features: {}\n", if f.features.is_empty() { "none".into() } else { f.features.join(", ") }));
    let options: Vec<String> = f.options.iter().map(|(name, on)| format!("{name} {}", if *on { "on" } else { "off" })).collect();
    out.push_str(&format!("Options: {}\n", options.join(", ")));
    out.push_str(&format!("Split-tunnel ranges: {}\n", f.split_ranges));
    out.push_str(&format!("Signed in: {}\n", if f.signed_in { "yes" } else { "no" }));
    out.push_str(&format!("Last server: {}\n", f.last_server.map(|s| s.to_string()).unwrap_or_else(|| "none".into())));
    let known: Vec<String> = f.known_servers.iter().map(|s| s.to_string()).collect();
    out.push_str(&format!("Servers with a device here: {}\n", if known.is_empty() { "none".into() } else { known.join(", ") }));
    let lines: Vec<&str> = f.log.lines().collect();
    let tail = &lines[lines.len().saturating_sub(LOG_LINES)..];
    out.push_str(&format!("\nApp log (last {} lines):\n", tail.len()));
    for l in tail {
        out.push_str(l);
        out.push('\n');
    }
    cap(&strip(&out), MAX_DIAGNOSTICS)
}

/// At most `max` characters, cut at a line where possible, keeping the start (version and state come first).
pub fn cap(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let cut: String = text.chars().take(max.saturating_sub(16)).collect();
    let cut = match cut.rfind('\n') {
        Some(i) if i > max / 2 => cut[..=i].to_string(),
        _ => cut,
    };
    format!("{cut}[cut: too long]")
}

/// Removes key lines, WireGuard-shaped keys, IP addresses and the user name in Windows paths.
pub fn strip(text: &str) -> String {
    let lines: Vec<String> = text.split('\n').map(|line| {
        let low = line.to_ascii_lowercase();
        if KEY_WORDS.iter().any(|w| low.contains(w)) {
            return REDACTED.to_string();
        }
        user_paths(&addresses(&keys(line)))
    }).collect();
    lines.join("\n")
}

fn is_b64(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '+' || c == '/'
}

/// A WireGuard key in base64: 43 characters then "=" (32 bytes), standing on its own.
fn keys(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        if is_b64(chars[i]) && (i == 0 || !is_b64(chars[i - 1])) {
            let mut j = i;
            while j < chars.len() && is_b64(chars[j]) {
                j += 1;
            }
            let pad = chars[j..].iter().take_while(|c| **c == '=').count();
            let after = chars.get(j + pad).copied();
            if j - i == 43 && pad == 1 && !after.is_some_and(is_b64) {
                out.push_str(REDACTED);
            } else {
                out.extend(&chars[i..j + pad]);
            }
            i = j + pad;
            continue;
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

fn word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// Every IPv4 address (four dotted numbers up to 255) and IPv6 address (hex groups with "::" or at least three colons) becomes ADDRESS.
fn addresses(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let starts = i == 0 || !word(chars[i - 1]);
        if starts && (chars[i].is_ascii_hexdigit() || chars[i] == ':') {
            let mut j = i;
            while j < chars.len() && (chars[j].is_ascii_hexdigit() || chars[j] == ':' || chars[j] == '.') {
                j += 1;
            }
            // trailing dots or colons end a sentence, they aren't part of the address
            let mut end = j;
            while end > i && (chars[end - 1] == '.' || chars[end - 1] == ':') {
                end -= 1;
            }
            let ends = end == chars.len() || !word(chars[end]) || end < j;
            let run: String = chars[i..end].iter().collect();
            let port = run.rsplit_once(':').filter(|(host, port)| ipv4(host) && !port.is_empty() && port.chars().all(|c| c.is_ascii_digit())).map(|(_, port)| port.to_string());
            if ends && (ipv4(&run) || ipv6(&run) || port.is_some()) {
                out.push_str(ADDRESS);
                if let Some(port) = port.filter(|_| !ipv6(&run)) {
                    out.push(':');
                    out.push_str(&port);
                }
                out.extend(&chars[end..j]);
                i = j;
                continue;
            }
            out.extend(&chars[i..j]);
            i = j;
            continue;
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

fn ipv4(s: &str) -> bool {
    s.parse::<std::net::Ipv4Addr>().is_ok()
}

fn ipv6(s: &str) -> bool {
    let colons = s.matches(':').count();
    (colons >= 3 || s.contains("::")) && s.chars().any(|c| c.is_ascii_hexdigit()) && s.parse::<std::net::Ipv6Addr>().is_ok()
}

/// "C:\Users\Name\..." becomes "C:\Users\[user]\...".
fn user_paths(line: &str) -> String {
    let mut out = String::new();
    let mut rest = line;
    loop {
        let low = rest.to_ascii_lowercase();
        let Some(at) = low.find("\\users\\").or_else(|| low.find("/users/")) else {
            out.push_str(rest);
            return out;
        };
        let start = at + 7;
        out.push_str(&rest[..start]);
        let after = &rest[start..];
        let end = after.find(['\\', '/', ' ', '"', '\'']).unwrap_or(after.len());
        if end > 0 {
            out.push_str("[user]");
        }
        rest = &after[end..];
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // WireGuard's documented example keys: not anyone's.
    const PRIV: &str = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=";
    const PUB: &str = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=";

    #[test]
    fn a_wireguard_config_loses_its_keys_and_addresses() {
        let config = format!("[Interface]\nPrivateKey = {PRIV}\nAddress = 10.66.0.9/32\nDNS = 10.66.0.1\n\n[Peer]\nPublicKey = {PUB}\nPresharedKey={PRIV}\nEndpoint = 2.31.133.78:51821\nAllowedIPs = 0.0.0.0/0, ::/0\n");
        let out = strip(&config);
        for gone in [PRIV, PUB, "PrivateKey", "PresharedKey", "10.66.0.9", "2.31.133.78", "10.66.0.1"] {
            assert!(!out.contains(gone), "{gone} in {out}");
        }
        assert!(out.contains("[Interface]") && out.contains("PublicKey = [redacted]") && out.contains("Endpoint = [address]:51821") && out.contains("Address = [address]/32"));
    }

    #[test]
    fn key_lines_go_in_any_spelling() {
        for line in ["PrivateKey = abc", "privatekey=abc", "  \"private_key\": \"abc\"", "PRESHAREDKEY = x", "preshared_key: y"] {
            assert_eq!(strip(line), REDACTED, "{line}");
        }
    }

    #[test]
    fn a_44_character_key_goes_anywhere_on_a_line_but_other_text_stays() {
        assert_eq!(strip(&format!("key was {PRIV}, sadly")), "key was [redacted], sadly");
        assert_eq!(strip(&format!("({PUB})")), "([redacted])");
        assert_eq!(strip(&format!("PublicKey={PUB}")), "PublicKey=[redacted]");
        let longer = format!("{PRIV}AAAA");
        assert_eq!(strip(&longer), longer, "not a key: too long");
        let no_pad = &PRIV[..43];
        assert_eq!(strip(no_pad), no_pad, "not a key: no padding");
        let text = "Connected to Frankfurt in 812 ms; State::Connected; version 2.1.0; at 12:30:45; 1700000000 connect: pressed, server 3";
        assert_eq!(strip(text), text);
    }

    #[test]
    fn ip_addresses_go_including_the_home_network() {
        let out = strip("router 192.168.1.1, lan 172.16.4.20/24, public 5.30.213.225. v6 2001:db8::1 and fe80::1%12 and ::1, mapped ::ffff:10.0.0.1");
        for gone in ["192.168", "172.16", "5.30.213.225", "2001:db8", "fe80", "10.0.0.1"] {
            assert!(!out.contains(gone), "{gone} in {out}");
        }
        assert!(out.contains("public [address]."), "{out}");
        assert_eq!(strip("999.1.1.1 is not an address"), "999.1.1.1 is not an address");
        assert_eq!(strip("dns:10.0.0.1 via [2001:db8::2]:51820"), "dns:[address] via [[address]]:51820");
    }

    #[test]
    fn the_windows_user_name_is_hidden_in_paths() {
        assert_eq!(strip(r"C:\Users\Avigad\AppData\Local\live.pricechat.kiwivpn\settings.json"), r"C:\Users\[user]\AppData\Local\live.pricechat.kiwivpn\settings.json");
        assert_eq!(strip("no path here"), "no path here");
    }

    #[test]
    fn the_report_has_the_state_and_the_log_tail_only_stripped_and_capped() {
        let log: String = (0..200).map(|i| format!("{i} connect: pressed, server 3 at 192.168.0.{}\n", i % 250)).collect();
        let f = Facts { app_version: "2.1.0".into(), service: "Running".into(), tunnel: "Connected".into(), handshake_age_seconds: Some(4), features: vec!["kill_switch".into()],
                        options: vec![("Smart Connect".into(), true), ("Kill switch".into(), false)], split_ranges: 2, signed_in: true, last_server: Some(3), known_servers: vec![3, 5],
                        log: format!("{log}PrivateKey = {PRIV}\n"), ..Facts::default() };
        let out = report(&f);
        assert!(out.starts_with("Kiwi VPN for Windows 2.1.0\n") && out.contains("Smart Connect on, Kill switch off") && out.contains("Split-tunnel ranges: 2"));
        assert!(out.contains("App log (last 60 lines)") && out.contains("199 connect") && !out.contains("\n139 connect"));
        assert!(!out.contains("192.168") && !out.contains(PRIV) && out.chars().count() <= MAX_DIAGNOSTICS);
        let huge = Facts { log: "x".repeat(400).repeat(100).chars().collect::<Vec<_>>().chunks(300).map(|c| c.iter().collect::<String>() + "\n").collect(), ..f };
        let out = report(&huge);
        assert!(out.chars().count() <= MAX_DIAGNOSTICS && out.ends_with("[cut: too long]"));
    }

    #[test]
    fn the_body_has_the_fields_price_chat_expects() {
        let b = body("bug", "  It broke at 192.168.1.4  ", "2.1.0", Some("diag"));
        assert_eq!(b, serde_json::json!({ "kind": "bug", "message": "It broke at [address]", "app_version": "2.1.0", "diagnostics": "diag" }));
        let b = body("feedback", "Nice", "2.1.0", None);
        assert_eq!(b, serde_json::json!({ "kind": "feedback", "message": "Nice", "app_version": "2.1.0" }));
    }

    #[test]
    fn the_input_is_checked_here_too() {
        assert!(check("bug", "x").is_ok() && check("feedback", "x").is_ok());
        assert!(check("rant", "x").is_err() && check("bug", "   ").is_err() && check("bug", &"x".repeat(MAX_MESSAGE + 1)).is_err());
        assert!(check("bug", &"x".repeat(MAX_MESSAGE)).is_ok());
    }
}
