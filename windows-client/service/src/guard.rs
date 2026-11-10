//! What the service blocks while a tunnel is wanted, as plain rules with tests. `firewall` turns them into Windows Filtering Platform filters.
//!
//! Two levels, both held only while someone wants the tunnel:
//! - always: DNS can't leave outside the tunnel. Windows asks every network's resolver, not just the VPN's, unless something stops it.
//! - kill switch: nothing leaves outside the tunnel except Kiwi's own two programs (so it can reconnect, switch servers and sign in), the
//!   local network if the person allows it, the ranges they chose to keep off the VPN, DHCP and IPv6 neighbour discovery.
//!
//! Safeguards against being left offline, by construction rather than by remembering to clean up:
//! - the filters belong to a dynamic WFP session: Windows deletes them the moment the service's process ends, however it ends;
//! - nothing is written to disk that would re-create them except the wanted tunnel itself, which Disconnect deletes;
//! - a tunnel that can't be brought back (see `protocol::recovery_given_up`) releases the block and says so.

use std::net::IpAddr;

/// One address range.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Net {
    pub ip: IpAddr,
    pub bits: u8,
}

impl Net {
    pub fn parse(text: &str) -> Result<Net, String> {
        let (ip, bits) = match text.split_once('/') {
            Some((ip, bits)) => (ip, Some(bits)),
            None => (text, None),
        };
        let ip: IpAddr = ip.trim().parse().map_err(|_| format!("{text} isn't an address range"))?;
        let max = if ip.is_ipv4() { 32 } else { 128 };
        let bits = match bits {
            Some(b) => b.trim().parse::<u8>().map_err(|_| format!("{text} isn't an address range"))?,
            None => max,
        };
        if bits > max {
            return Err(format!("{text} isn't an address range"));
        }
        Ok(Net { ip: masked(ip, bits), bits })
    }
    pub fn v6(&self) -> bool {
        self.ip.is_ipv6()
    }
    /// The IPv4 address and mask as numbers, for WFP.
    pub fn v4_parts(&self) -> Option<(u32, u32)> {
        match self.ip {
            IpAddr::V4(a) => Some((u32::from(a), if self.bits == 0 { 0 } else { u32::MAX << (32 - self.bits) })),
            IpAddr::V6(_) => None,
        }
    }
}

fn masked(ip: IpAddr, bits: u8) -> IpAddr {
    match ip {
        IpAddr::V4(a) => {
            let m = if bits == 0 { 0 } else { u32::MAX << (32 - bits) };
            IpAddr::V4((u32::from(a) & m).into())
        }
        IpAddr::V6(a) => {
            let m = if bits == 0 { 0 } else { u128::MAX << (128 - bits) };
            IpAddr::V6((u128::from(a) & m).into())
        }
    }
}

/// The local network: private ranges, link-local, multicast and broadcast.
pub const LAN: &[&str] = &["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255/32", "fe80::/10", "fc00::/7", "ff00::/8"];
/// Ranges the person may keep off the VPN, at most.
pub const MAX_ALLOW: usize = 64;

/// What the app asked for with a connect. Checked again here: the service never trusts it.
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Protect {
    #[serde(default)]
    pub kill_switch: bool,
    #[serde(default)]
    pub allow_lan: bool,
    /// The person's split-tunnel ranges, which go outside the VPN on purpose, so the kill switch lets them through.
    #[serde(default)]
    pub allow: Vec<String>,
}

impl Protect {
    pub fn check(&self) -> Result<Vec<Net>, String> {
        if self.allow.len() > MAX_ALLOW {
            return Err("too many ranges kept off the VPN".into());
        }
        let nets = self.allow.iter().map(|a| Net::parse(a)).collect::<Result<Vec<_>, _>>()?;
        // a range this wide isn't split tunneling, it's turning the kill switch off
        if nets.iter().any(|n| n.bits < if n.v6() { 16 } else { 8 }) {
            return Err("a range kept off the VPN is too wide".into());
        }
        Ok(nets)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dir {
    Out,
    In,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Cond {
    Loopback,
    /// One of the `apps` given to `rules`, by index.
    App(usize),
    LocalNet(Net),
    RemoteNet(Net),
    RemotePort(u16),
    LocalPort(u16),
    Protocol(u8),
}

pub const UDP: u8 = 17;
pub const ICMPV6: u8 = 58;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rule {
    pub v6: bool,
    pub dir: Dir,
    pub permit: bool,
    /// Higher wins within Kiwi's sublayer.
    pub weight: u8,
    /// All must match.
    pub conds: Vec<Cond>,
    pub name: &'static str,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    pub kill_switch: bool,
    pub allow_lan: bool,
    /// The tunnel's own addresses: traffic from them is inside the VPN.
    pub tunnel: Vec<Net>,
    pub allow: Vec<Net>,
    /// How many programs `Cond::App` may refer to (Kiwi's service and app).
    pub apps: usize,
}

pub const W_LOOPBACK: u8 = 15;
pub const W_TUNNEL: u8 = 14;
pub const W_DNS_BLOCK: u8 = 13;
pub const W_APPS: u8 = 12;
pub const W_BASICS: u8 = 9;
pub const W_ALLOWED: u8 = 8;
pub const W_BLOCK_ALL: u8 = 0;

/// The filters for a plan. Every rule exists for IPv4 and IPv6 where it means anything.
pub fn rules(plan: &Plan) -> Vec<Rule> {
    let mut out = Vec::new();
    let both = |out: &mut Vec<Rule>, dir: Dir, permit: bool, weight: u8, conds: Vec<Cond>, name: &'static str| {
        for v6 in [false, true] {
            out.push(Rule { v6, dir, permit, weight, conds: conds.clone(), name });
        }
    };
    for dir in [Dir::Out, Dir::In] {
        both(&mut out, dir, true, W_LOOPBACK, vec![Cond::Loopback], "loopback");
        for net in &plan.tunnel {
            out.push(Rule { v6: net.v6(), dir, permit: true, weight: W_TUNNEL, conds: vec![Cond::LocalNet(*net)], name: "inside the tunnel" });
        }
    }
    // DNS outside the tunnel: the tunnel's own DNS comes from a tunnel address, so the rule above lets it through first
    both(&mut out, Dir::Out, false, W_DNS_BLOCK, vec![Cond::RemotePort(53)], "DNS outside the tunnel");
    if !plan.kill_switch {
        return out;
    }
    for dir in [Dir::Out, Dir::In] {
        for app in 0..plan.apps {
            both(&mut out, dir, true, W_APPS, vec![Cond::App(app)], "Kiwi VPN's own programs");
        }
    }
    out.push(Rule { v6: false, dir: Dir::Out, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(UDP), Cond::RemotePort(67)], name: "DHCP" });
    out.push(Rule { v6: false, dir: Dir::In, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(UDP), Cond::LocalPort(68)], name: "DHCP" });
    out.push(Rule { v6: true, dir: Dir::Out, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(UDP), Cond::RemotePort(547)], name: "DHCPv6" });
    out.push(Rule { v6: true, dir: Dir::In, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(UDP), Cond::LocalPort(546)], name: "DHCPv6" });
    for dir in [Dir::Out, Dir::In] {
        out.push(Rule { v6: true, dir, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(ICMPV6)], name: "IPv6 neighbour discovery" });
    }
    let mut allowed: Vec<(Net, &'static str)> = plan.allow.iter().map(|n| (*n, "kept off the VPN")).collect();
    if plan.allow_lan {
        allowed.extend(LAN.iter().filter_map(|l| Net::parse(l).ok()).map(|n| (n, "local network")));
    }
    for (net, name) in allowed {
        for dir in [Dir::Out, Dir::In] {
            out.push(Rule { v6: net.v6(), dir, permit: true, weight: W_ALLOWED, conds: vec![Cond::RemoteNet(net)], name });
        }
    }
    both(&mut out, Dir::Out, false, W_BLOCK_ALL, vec![], "kill switch");
    both(&mut out, Dir::In, false, W_BLOCK_ALL, vec![], "kill switch");
    out
}

/// The tunnel's addresses from a config's Address line (already checked by `config::parse`).
pub fn tunnel_nets(addresses: &[String]) -> Vec<Net> {
    addresses.iter().filter_map(|a| Net::parse(a).ok()).collect()
}

/// WireGuard turns on its own firewall when a peer's AllowedIPs holds a default route. It can't allow the local network and disappears
/// whenever the tunnel's process does, so Kiwi's rules above replace it: each default route is sent as its two halves instead, which
/// routes exactly the same traffic.
pub fn without_default_routes(allowed: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for a in allowed {
        match a.as_str() {
            "0.0.0.0/0" => out.extend(["0.0.0.0/1".to_string(), "128.0.0.0/1".to_string()]),
            "::/0" => out.extend(["::/1".to_string(), "8000::/1".to_string()]),
            other => out.push(other.to_string()),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan(kill: bool, lan: bool) -> Plan {
        Plan { kill_switch: kill, allow_lan: lan, tunnel: tunnel_nets(&["10.66.0.5/32".into(), "fd66:66::5/128".into()]), allow: vec![Net::parse("1.0.0.1/32").unwrap()], apps: 2 }
    }

    fn permits(rules: &[Rule], v6: bool, dir: Dir, c: &Cond) -> Option<u8> {
        rules.iter().filter(|r| r.permit && r.v6 == v6 && r.dir == dir && r.conds.contains(c)).map(|r| r.weight).max()
    }

    #[test]
    fn ranges_read_and_mask() {
        assert_eq!(Net::parse("10.1.2.3/8").unwrap(), Net { ip: "10.0.0.0".parse().unwrap(), bits: 8 });
        assert_eq!(Net::parse("1.1.1.1").unwrap().bits, 32);
        assert_eq!(Net::parse("fd66:66::5/64").unwrap().ip, "fd66:66::".parse::<IpAddr>().unwrap());
        assert_eq!(Net::parse("192.168.1.0/24").unwrap().v4_parts(), Some((0xC0A8_0100, 0xFFFF_FF00)));
        assert_eq!(Net::parse("0.0.0.0/0").unwrap().v4_parts(), Some((0, 0)));
        for bad in ["", "1.1.1.1/33", "x/8", "::/129", "1.1.1.1/-1"] {
            assert!(Net::parse(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn without_the_kill_switch_only_dns_outside_the_tunnel_is_blocked() {
        let r = rules(&plan(false, true));
        assert!(r.iter().filter(|r| !r.permit).all(|r| r.conds == vec![Cond::RemotePort(53)] && r.dir == Dir::Out));
        assert_eq!(r.iter().filter(|r| !r.permit).count(), 2, "IPv4 and IPv6");
        let tunnel = Cond::LocalNet(Net::parse("10.66.0.5/32").unwrap());
        assert!(permits(&r, false, Dir::Out, &tunnel).unwrap() > W_DNS_BLOCK, "the VPN's own DNS goes through");
        assert!(permits(&r, false, Dir::Out, &Cond::Loopback).unwrap() > W_DNS_BLOCK, "a local resolver still works");
        assert!(!r.iter().any(|r| r.conds.contains(&Cond::App(0))), "nothing else is touched");
    }

    #[test]
    fn the_kill_switch_blocks_everything_else_in_both_families_and_directions() {
        let r = rules(&plan(true, false));
        for v6 in [false, true] {
            for dir in [Dir::Out, Dir::In] {
                assert!(r.iter().any(|x| !x.permit && x.v6 == v6 && x.dir == dir && x.conds.is_empty() && x.weight == W_BLOCK_ALL), "{v6} {dir:?}");
                assert!(permits(&r, v6, dir, &Cond::App(0)).unwrap() > W_BLOCK_ALL && permits(&r, v6, dir, &Cond::App(1)).is_some(), "Kiwi can still reconnect");
            }
        }
        assert!(permits(&r, false, Dir::Out, &Cond::RemoteNet(Net::parse("1.0.0.1/32").unwrap())).is_some(), "split-tunnel ranges go through");
        assert!(permits(&r, false, Dir::Out, &Cond::RemoteNet(Net::parse("192.168.0.0/16").unwrap())).is_none(), "no local network unless allowed");
        assert!(r.iter().any(|x| x.permit && x.conds.contains(&Cond::RemotePort(67))), "DHCP keeps the PC's address");
        // DNS to the local router is still blocked even though the LAN is allowed: it's the leak that matters most
        let lan = rules(&plan(true, true));
        assert!(permits(&lan, false, Dir::Out, &Cond::RemoteNet(Net::parse("192.168.0.0/16").unwrap())).unwrap() < W_DNS_BLOCK);
        assert!(permits(&lan, true, Dir::In, &Cond::RemoteNet(Net::parse("fe80::/10").unwrap())).is_some());
    }

    #[test]
    fn every_rule_s_ranges_match_its_family() {
        for r in rules(&plan(true, true)) {
            for c in &r.conds {
                if let Cond::LocalNet(n) | Cond::RemoteNet(n) = c {
                    assert_eq!(n.v6(), r.v6, "{}", r.name);
                }
            }
        }
    }

    #[test]
    fn the_app_s_ranges_are_checked_again() {
        assert_eq!(Protect { allow: vec!["192.168.1.0/24".into()], ..Default::default() }.check().unwrap().len(), 1);
        assert!(Protect { allow: vec!["0.0.0.0/0".into()], ..Default::default() }.check().is_err());
        assert!(Protect { allow: vec!["10.0.0.0/7".into()], ..Default::default() }.check().is_err());
        assert!(Protect { allow: vec!["::/8".into()], ..Default::default() }.check().is_err());
        assert!(Protect { allow: vec!["nonsense".into()], ..Default::default() }.check().is_err());
        assert!(Protect { allow: vec!["1.1.1.1/32".into(); MAX_ALLOW + 1], ..Default::default() }.check().is_err());
        assert!(serde_json::from_str::<Protect>(r#"{"kill_switch":true,"run":"x"}"#).is_err(), "nothing unexpected");
        assert_eq!(serde_json::from_str::<Protect>("{}").unwrap(), Protect::default());
    }

    #[test]
    fn default_routes_become_halves_and_nothing_else_changes() {
        let a = without_default_routes(&["0.0.0.0/0".into(), "::/0".into()]);
        assert_eq!(a, ["0.0.0.0/1", "128.0.0.0/1", "::/1", "8000::/1"]);
        let split = without_default_routes(&["0.0.0.0/5".into(), "8.0.0.0/7".into(), "::/0".into()]);
        assert_eq!(split, ["0.0.0.0/5", "8.0.0.0/7", "::/1", "8000::/1"]);
        assert!(!a.iter().any(|x| x.ends_with("/0")), "WireGuard's own firewall stays off");
    }
}
