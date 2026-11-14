//! The only tunnel configuration the service will accept. It runs as SYSTEM, so a config is treated as hostile: anything unknown is refused, above all the
//! script hooks (PreUp, PostUp and so on) that would run commands. What goes on to the tunnel is rebuilt from the parsed values, never the text it was given.

use base64::{engine::general_purpose::STANDARD, Engine};
use std::net::{IpAddr, SocketAddr};

pub const MAX_BYTES: usize = 8 * 1024;
const MAX_ALLOWED: usize = 64;

#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    pub private_key: String,
    pub addresses: Vec<String>,
    pub dns: Vec<IpAddr>,
    pub peer_public_key: String,
    pub endpoint: String,
    pub allowed_ips: Vec<String>,
    pub keepalive: u16,
}

fn key(value: &str) -> Result<String, String> {
    match STANDARD.decode(value) {
        Ok(raw) if raw.len() == 32 => Ok(value.to_string()),
        _ => Err("a key isn't a valid 32-byte key".into()),
    }
}

fn cidr(value: &str) -> Result<String, String> {
    let (ip, bits) = value.split_once('/').ok_or("an address has no prefix length")?;
    let ip: IpAddr = ip.parse().map_err(|_| "an address isn't an IP address")?;
    let bits: u8 = bits.parse().map_err(|_| "a prefix length isn't a number")?;
    let max = if ip.is_ipv4() { 32 } else { 128 };
    if bits > max {
        return Err("a prefix length is too long".into());
    }
    Ok(format!("{ip}/{bits}"))
}

/// The tunnel's own address: exactly one host, /32 or /128 (audit L13). The kill switch lets everything from these addresses through as
/// "inside the tunnel", so a wider prefix (0.0.0.0/0 at worst) would turn that rule into a hole.
fn host_address(value: &str) -> Result<String, String> {
    let checked = cidr(value)?;
    let (ip, bits) = checked.split_once('/').unwrap_or(("", ""));
    let host = if ip.contains(':') { "128" } else { "32" };
    if bits != host {
        return Err("the tunnel's address must be a single address (/32 or /128)".into());
    }
    Ok(checked)
}

fn endpoint(value: &str) -> Result<String, String> {
    if value.parse::<SocketAddr>().is_ok() {
        return Ok(value.to_string());
    }
    let (host, port) = value.rsplit_once(':').ok_or("the endpoint has no port")?;
    port.parse::<u16>().map_err(|_| "the endpoint's port isn't a number")?;
    let good = !host.is_empty() && host.len() <= 253 && host.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-');
    if !good {
        return Err("the endpoint isn't a host name or address".into());
    }
    Ok(value.to_string())
}

fn list(value: &str) -> impl Iterator<Item = &str> {
    value.split(',').map(str::trim).filter(|v| !v.is_empty())
}

/// Parses the config the Price Chat backend issued for one device.
pub fn parse(text: &str) -> Result<Config, String> {
    if text.len() > MAX_BYTES {
        return Err("the config is too large".into());
    }
    let (mut section, mut interfaces, mut peers) = ("", 0, 0);
    let (mut private_key, mut peer_key, mut end) = (None, None, None);
    let (mut addresses, mut dns, mut allowed, mut keepalive) = (Vec::new(), Vec::new(), Vec::new(), 0u16);
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if line.eq_ignore_ascii_case("[interface]") {
            section = "interface";
            interfaces += 1;
            continue;
        }
        if line.eq_ignore_ascii_case("[peer]") {
            section = "peer";
            peers += 1;
            continue;
        }
        let (name, value) = line.split_once('=').ok_or("the config has a line it can't read")?;
        let (name, value) = (name.trim().to_ascii_lowercase(), value.trim());
        match (section, name.as_str()) {
            ("interface", "privatekey") => private_key = Some(key(value)?),
            ("interface", "address") => {
                for a in list(value) {
                    addresses.push(host_address(a)?);
                }
            }
            ("interface", "dns") => {
                for d in list(value) {
                    dns.push(d.parse::<IpAddr>().map_err(|_| "a DNS server isn't an IP address")?);
                }
            }
            ("peer", "publickey") => peer_key = Some(key(value)?),
            ("peer", "endpoint") => end = Some(endpoint(value)?),
            ("peer", "allowedips") => {
                for a in list(value) {
                    allowed.push(cidr(a)?);
                }
            }
            ("peer", "persistentkeepalive") => keepalive = value.parse().map_err(|_| "the keepalive isn't a number")?,
            (_, other) => return Err(format!("the config has a setting that isn't allowed: {other}")),
        }
    }
    if interfaces != 1 || peers != 1 {
        return Err("the config must have one interface and one peer".into());
    }
    if addresses.is_empty() || allowed.is_empty() || allowed.len() > MAX_ALLOWED {
        return Err("the config needs an address and allowed addresses".into());
    }
    Ok(Config {
        private_key: private_key.ok_or("the config has no private key")?,
        addresses,
        dns,
        peer_public_key: peer_key.ok_or("the config has no server key")?,
        endpoint: end.ok_or("the config has no server address")?,
        allowed_ips: allowed,
        keepalive,
    })
}

impl Config {
    /// What the tunnel reads, rebuilt from the checked values.
    pub fn render(&self) -> String {
        let join = |v: &[String]| v.join(", ");
        let mut out = format!("[Interface]\nPrivateKey = {}\nAddress = {}\n", self.private_key, join(&self.addresses));
        if !self.dns.is_empty() {
            out += &format!("DNS = {}\n", self.dns.iter().map(ToString::to_string).collect::<Vec<_>>().join(", "));
        }
        out += &format!("\n[Peer]\nPublicKey = {}\nEndpoint = {}\nAllowedIPs = {}\n", self.peer_public_key, self.endpoint, join(&self.allowed_ips));
        if self.keepalive > 0 {
            out += &format!("PersistentKeepalive = {}\n", self.keepalive);
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const K: &str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";

    fn good() -> String {
        format!("[Interface]\nPrivateKey = {K}\nAddress = 10.66.0.5/32, fd66:66::5/128\nDNS = 1.1.1.1\n\n[Peer]\nPublicKey = {K}\nEndpoint = 2.29.149.29:51821\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n")
    }

    #[test]
    fn reads_what_the_backend_issues_and_rebuilds_it() {
        let c = parse(&good()).unwrap();
        assert_eq!(c.endpoint, "2.29.149.29:51821");
        assert_eq!(c.allowed_ips, ["0.0.0.0/0", "::/0"]);
        assert_eq!(parse(&c.render()).unwrap(), c);
    }

    #[test]
    fn refuses_anything_that_could_run_a_command() {
        for hook in ["PreUp", "PostUp", "PreDown", "PostDown", "Table", "SaveConfig"] {
            let text = good().replace("[Peer]", &format!("{hook} = calc.exe\n[Peer]"));
            assert!(parse(&text).unwrap_err().contains("isn't allowed"), "{hook}");
        }
    }

    #[test]
    fn hostile_values_never_reach_the_output() {
        assert!(parse(&good().replace("2.29.149.29:51821", "evil\nPostUp=calc:1")).is_err());
        assert!(parse(&good().replace("1.1.1.1", "1.1.1.1\nPostUp = calc")).is_err());
        assert!(parse(&good().replace("2.29.149.29:51821", "a b:1")).is_err());
        assert!(parse(&good().replace("2.29.149.29:51821", "no-port")).is_err());
        assert!(!parse(&good()).unwrap().render().contains("PostUp"));
    }

    #[test]
    fn refuses_bad_shapes() {
        assert!(parse("").is_err());
        assert!(parse(&format!("{}\n[Peer]\nPublicKey = {K}", good())).is_err());
        assert!(parse(&good().replace(K, "short")).is_err());
        assert!(parse(&good().replace("10.66.0.5/32", "10.66.0.5/99")).is_err());
        assert!(parse(&good().replace("10.66.0.5/32", "10.66.0.5")).is_err());
        assert!(parse(&"#".repeat(MAX_BYTES + 1)).is_err());
    }

    #[test]
    fn l13_the_tunnel_s_address_is_a_single_host() {
        for wide in ["10.66.0.5/24", "10.66.0.5/31", "0.0.0.0/0", "10.66.0.5/0"] {
            assert!(parse(&good().replace("10.66.0.5/32", wide)).unwrap_err().contains("single address"), "{wide}");
        }
        for wide in ["fd66:66::5/64", "fd66:66::5/127", "::/0"] {
            assert!(parse(&good().replace("fd66:66::5/128", wide)).unwrap_err().contains("single address"), "{wide}");
        }
        assert_eq!(parse(&good()).unwrap().addresses, ["10.66.0.5/32", "fd66:66::5/128"]);
        // AllowedIPs are routes, not the tunnel's address: wide prefixes are normal there
        assert_eq!(parse(&good()).unwrap().allowed_ips, ["0.0.0.0/0", "::/0"]);
    }

    #[test]
    fn a_host_name_endpoint_works() {
        let c = parse(&good().replace("2.29.149.29:51821", "hel1.vpn.pricechat.live:51821")).unwrap();
        assert_eq!(c.endpoint, "hel1.vpn.pricechat.live:51821");
    }
}
