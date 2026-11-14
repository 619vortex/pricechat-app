//! What the service blocks while a tunnel is wanted, as plain rules with tests. `firewall` turns them into Windows Filtering Platform filters.
//!
//! Two levels, both held only while someone wants the tunnel:
//! - always: DNS can't leave outside the tunnel. Windows asks every network's resolver, not just the VPN's, unless something stops it.
//! - kill switch: nothing leaves outside the tunnel except the tunnel's own UDP to its server, Kiwi's window over HTTPS (so it can sign
//!   in and fetch the next server's settings), the local network if the person allows it, the ranges they chose to keep off the VPN,
//!   DHCP to the local network and IPv6 neighbour discovery on the link (2.2: audit L10-L12, L14).
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
    /// 2.2, audit M3: the kill switch's filters are also kept persistently (and at boot), so they hold while the service restarts,
    /// before it starts, and through every automatic give-up. Only Disconnect or `kiwivpn-service unblock` removes them. Needs
    /// `kill_switch`. Left out of the request when off, so a 2.1 service (which refuses unknown fields) still understands it.
    #[serde(default, skip_serializing_if = "is_false")]
    pub strict: bool,
    /// 2.2, audit L14: with the kill switch on, DNS over TLS (port 853) is blocked outside the tunnel too, including to the local
    /// network and the ranges kept off the VPN. Left out when off, like `strict`.
    #[serde(default, skip_serializing_if = "is_false")]
    pub block_dot: bool,
}

fn is_false(b: &bool) -> bool {
    !*b
}

impl Protect {
    pub fn check(&self) -> Result<Vec<Net>, String> {
        if self.allow.len() > MAX_ALLOW {
            return Err("too many ranges kept off the VPN".into());
        }
        if self.strict && !self.kill_switch {
            return Err("the strict kill switch needs the kill switch".into());
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
    /// One of the `apps` given to `rules`, by index (APP_SERVICE, APP_WINDOW).
    App(usize),
    LocalNet(Net),
    RemoteNet(Net),
    RemotePort(u16),
    LocalPort(u16),
    Protocol(u8),
    /// An ICMP or ICMPv6 type. WFP's ALE layers carry it in the local port field (FWPM_CONDITION_ICMP_TYPE is
    /// FWPM_CONDITION_IP_LOCAL_PORT), which is how `firewall` sends it.
    IcmpType(u8),
}

pub const TCP: u8 = 6;
pub const UDP: u8 = 17;
pub const ICMPV6: u8 = 58;
/// `Cond::App` indexes: the service (which also runs the tunnel's process) and the window.
pub const APP_SERVICE: usize = 0;
pub const APP_WINDOW: usize = 1;
/// The only port the window may reach outside the tunnel under the kill switch: HTTPS, for signing in, the next server's settings
/// and timing servers.
pub const APP_PORT: u16 = 443;
/// IPv6 neighbour discovery: router solicitation and advertisement, neighbour solicitation and advertisement, redirect.
pub const ND_TYPES: std::ops::RangeInclusive<u8> = 133..=137;
/// Where neighbour discovery and DHCPv6 happen: link-local and link-scope multicast.
pub const LINK_V6: &[&str] = &["fe80::/10", "ff02::/16"];
/// Where DHCPv4 requests may go: broadcast and the private and link-local ranges a DHCP server lives in.
pub const DHCP_V4: &[&str] = &["255.255.255.255/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"];
pub const DOT_PORT: u16 = 853;

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
    /// The server's address and port, once resolved: the one place the tunnel's own UDP may go outside the tunnel. None (a host
    /// name that couldn't be looked up) lets the service send UDP anywhere instead, as before 2.2.
    pub endpoint: Option<std::net::SocketAddr>,
    /// Block DNS over TLS outside the tunnel (audit L14). Only with the kill switch.
    pub block_dot: bool,
}

pub const W_LOOPBACK: u8 = 15;
pub const W_TUNNEL: u8 = 14;
pub const W_DNS_BLOCK: u8 = 13;
pub const W_APPS: u8 = 12;
pub const W_BASICS: u8 = 9;
pub const W_ALLOWED: u8 = 8;
pub const W_BLOCK_ALL: u8 = 0;

fn host_net(ip: IpAddr) -> Net {
    Net { ip, bits: if ip.is_ipv4() { 32 } else { 128 } }
}

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
    // L14: DNS over TLS too, above the local network and the ranges kept off the VPN
    if plan.block_dot {
        both(&mut out, Dir::Out, false, W_DNS_BLOCK, vec![Cond::Protocol(TCP), Cond::RemotePort(DOT_PORT)], "DNS over TLS outside the tunnel");
    }
    // L12: the window only reaches HTTPS. Replies to a flow it opened need no inbound rule: WFP's ALE layers judge a flow once.
    if plan.apps > APP_WINDOW {
        both(&mut out, Dir::Out, true, W_APPS, vec![Cond::App(APP_WINDOW), Cond::Protocol(TCP), Cond::RemotePort(APP_PORT)], "Kiwi VPN's window (HTTPS only)");
    }
    // L12: the tunnel's UDP goes only to its server. No program condition: WireGuard's driver sends it from the kernel, so it can't
    // be told apart by program, and UDP to the server's WireGuard port is the tunnel whoever sends it.
    match plan.endpoint {
        Some(ep) => {
            let server = host_net(ep.ip());
            for dir in [Dir::Out, Dir::In] {
                out.push(Rule { v6: server.v6(), dir, permit: true, weight: W_APPS, conds: vec![Cond::Protocol(UDP), Cond::RemoteNet(server), Cond::RemotePort(ep.port())], name: "the VPN server (tunnel UDP)" });
            }
        }
        None if plan.apps > APP_SERVICE => both(&mut out, Dir::Out, true, W_APPS, vec![Cond::App(APP_SERVICE), Cond::Protocol(UDP)], "Kiwi VPN's service (UDP, server not yet known)"),
        None => {}
    }
    // L11: DHCPv4 requests only to broadcast and the local ranges; replies only from a DHCP server's port to the client's
    for net in DHCP_V4.iter().filter_map(|n| Net::parse(n).ok()) {
        out.push(Rule { v6: false, dir: Dir::Out, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(UDP), Cond::LocalPort(68), Cond::RemotePort(67), Cond::RemoteNet(net)], name: "DHCP" });
    }
    out.push(Rule { v6: false, dir: Dir::In, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(UDP), Cond::LocalPort(68), Cond::RemotePort(67)], name: "DHCP" });
    let link: Vec<Net> = LINK_V6.iter().filter_map(|n| Net::parse(n).ok()).collect();
    for net in &link {
        for dir in [Dir::Out, Dir::In] {
            out.push(Rule { v6: true, dir, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(UDP), Cond::LocalPort(546), Cond::RemotePort(547), Cond::RemoteNet(*net)], name: "DHCPv6" });
        }
    }
    // L10: neighbour discovery only (types 133-137), only on the link. Ping and every other ICMPv6 outside the tunnel is blocked.
    for t in ND_TYPES {
        for net in &link {
            for dir in [Dir::Out, Dir::In] {
                out.push(Rule { v6: true, dir, permit: true, weight: W_BASICS, conds: vec![Cond::Protocol(ICMPV6), Cond::IcmpType(t), Cond::RemoteNet(*net)], name: "IPv6 neighbour discovery" });
            }
        }
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

impl Net {
    pub fn contains(&self, ip: IpAddr) -> bool {
        ip.is_ipv4() == self.ip.is_ipv4() && masked(ip, self.bits) == self.ip
    }
}

/// One connection attempt, as WFP's ALE layers see it. For ICMP, `local_port` is the type (see `Cond::IcmpType`).
#[derive(Debug, Clone, Copy)]
pub struct Packet {
    pub dir: Dir,
    pub app: Option<usize>,
    pub protocol: u8,
    pub local: IpAddr,
    pub remote: IpAddr,
    pub local_port: u16,
    pub remote_port: u16,
    pub loopback: bool,
}

impl Cond {
    fn matches(&self, p: &Packet) -> bool {
        match self {
            Cond::Loopback => p.loopback,
            Cond::App(i) => p.app == Some(*i),
            Cond::LocalNet(n) => n.contains(p.local),
            Cond::RemoteNet(n) => n.contains(p.remote),
            Cond::RemotePort(port) => p.remote_port == *port,
            Cond::LocalPort(port) => p.local_port == *port,
            Cond::Protocol(proto) => p.protocol == *proto,
            Cond::IcmpType(t) => p.local_port == u16::from(*t),
        }
    }
}

/// How WFP judges a packet within Kiwi's sublayer: the highest-weight matching rule decides; nothing matching means permit (Kiwi's
/// rules only ever add blocks). Used by the tests to check behaviour, not just the list of rules.
pub fn verdict(rules: &[Rule], p: &Packet) -> bool {
    let v6 = p.remote.is_ipv6();
    rules
        .iter()
        .filter(|r| r.v6 == v6 && r.dir == p.dir && r.conds.iter().all(|c| c.matches(p)))
        .max_by_key(|r| (r.weight, !r.permit)) // equal weights: a block wins, as WFP's arbitration does
        .is_none_or(|r| r.permit)
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

    const SERVER: &str = "2.29.149.29:51821";

    fn plan(kill: bool, lan: bool) -> Plan {
        Plan {
            kill_switch: kill,
            allow_lan: lan,
            tunnel: tunnel_nets(&["10.66.0.5/32".into(), "fd66:66::5/128".into()]),
            allow: vec![Net::parse("1.0.0.1/32").unwrap()],
            apps: 2,
            endpoint: Some(SERVER.parse().unwrap()),
            block_dot: false,
        }
    }

    fn permits(rules: &[Rule], v6: bool, dir: Dir, c: &Cond) -> Option<u8> {
        rules.iter().filter(|r| r.permit && r.v6 == v6 && r.dir == dir && r.conds.contains(c)).map(|r| r.weight).max()
    }

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    /// An outgoing connection from this PC's ordinary address (or the tunnel's, with `from`).
    fn out(app: Option<usize>, protocol: u8, remote: &str, port: u16) -> Packet {
        let remote = ip(remote);
        let local = if remote.is_ipv4() { ip("192.168.1.20") } else { ip("2a02:1:2::20") };
        Packet { dir: Dir::Out, app, protocol, local, remote, local_port: 50_000, remote_port: port, loopback: false }
    }

    fn icmpv6(dir: Dir, kind: u8, remote: &str) -> Packet {
        Packet { dir, app: None, protocol: ICMPV6, local: ip("fe80::20"), remote: ip(remote), local_port: u16::from(kind), remote_port: 0, loopback: false }
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
        let lan = Net::parse("192.168.0.0/16").unwrap();
        assert!(lan.contains(ip("192.168.4.4")) && !lan.contains(ip("192.169.0.1")) && !lan.contains(ip("::1")));
    }

    #[test]
    fn without_the_kill_switch_only_dns_outside_the_tunnel_is_blocked() {
        let r = rules(&plan(false, true));
        assert!(r.iter().filter(|r| !r.permit).all(|r| r.conds == vec![Cond::RemotePort(53)] && r.dir == Dir::Out));
        assert_eq!(r.iter().filter(|r| !r.permit).count(), 2, "IPv4 and IPv6");
        let tunnel = Cond::LocalNet(Net::parse("10.66.0.5/32").unwrap());
        assert!(permits(&r, false, Dir::Out, &tunnel).unwrap() > W_DNS_BLOCK, "the VPN's own DNS goes through");
        assert!(permits(&r, false, Dir::Out, &Cond::Loopback).unwrap() > W_DNS_BLOCK, "a local resolver still works");
        assert!(!r.iter().any(|r| r.conds.contains(&Cond::App(0)) || r.conds.contains(&Cond::App(1))), "nothing else is touched");
        assert!(verdict(&r, &out(None, TCP, "1.1.1.1", 443)));
        assert!(!verdict(&r, &out(None, UDP, "8.8.8.8", 53)));
        let dot = Plan { block_dot: true, ..plan(false, true) };
        assert_eq!(rules(&dot), r, "DNS over TLS is only blocked with the kill switch on");
    }

    #[test]
    fn the_kill_switch_blocks_everything_else_in_both_families_and_directions() {
        let r = rules(&plan(true, false));
        for v6 in [false, true] {
            for dir in [Dir::Out, Dir::In] {
                assert!(r.iter().any(|x| !x.permit && x.v6 == v6 && x.dir == dir && x.conds.is_empty() && x.weight == W_BLOCK_ALL), "{v6} {dir:?}");
            }
        }
        assert!(!verdict(&r, &out(None, TCP, "1.1.1.1", 443)), "any program to the internet");
        assert!(!verdict(&r, &out(None, TCP, "2606:4700:4700::1111", 443)), "and over IPv6");
        assert!(!verdict(&r, &out(None, UDP, "8.8.8.8", 53)));
        assert!(verdict(&r, &out(None, TCP, "1.0.0.1", 443)), "split-tunnel ranges go through");
        assert!(!verdict(&r, &out(None, TCP, "192.168.1.1", 80)), "no local network unless allowed");
        let inside = Packet { local: ip("10.66.0.5"), ..out(None, TCP, "1.1.1.1", 443) };
        assert!(verdict(&r, &inside), "anything inside the tunnel");
        let lo = Packet { loopback: true, ..out(None, TCP, "127.0.0.1", 8080) };
        assert!(verdict(&r, &lo), "loopback");
        // DNS to the local router is still blocked even though the LAN is allowed: it's the leak that matters most
        let lan = rules(&plan(true, true));
        assert!(verdict(&lan, &out(None, TCP, "192.168.1.1", 80)));
        assert!(!verdict(&lan, &out(None, UDP, "192.168.1.1", 53)));
        assert!(verdict(&lan, &Packet { dir: Dir::In, ..out(None, TCP, "fe80::1", 445) }));
    }

    #[test]
    fn l10_icmpv6_only_neighbour_discovery_on_the_link() {
        let r = rules(&plan(true, false));
        for t in ND_TYPES {
            for remote in ["fe80::1", "ff02::1", "ff02::1:ff00:20"] {
                for dir in [Dir::Out, Dir::In] {
                    assert!(verdict(&r, &icmpv6(dir, t, remote)), "type {t} {remote} {dir:?}");
                }
            }
            assert!(!verdict(&r, &icmpv6(Dir::Out, t, "2001:db8::1")), "type {t} off the link");
            assert!(!verdict(&r, &icmpv6(Dir::Out, t, "ff05::2")), "type {t} beyond link scope");
        }
        for t in [128u8, 129, 1, 2, 3, 4, 130, 132, 138, 143] {
            assert!(!verdict(&r, &icmpv6(Dir::Out, t, "2606:4700:4700::1111")), "type {t} to the internet");
            assert!(!verdict(&r, &icmpv6(Dir::Out, t, "fe80::1")), "type {t} even on the link");
            assert!(!verdict(&r, &icmpv6(Dir::In, t, "2606:4700:4700::1111")), "type {t} in");
        }
        for rule in r.iter().filter(|x| x.conds.contains(&Cond::Protocol(ICMPV6))) {
            assert!(rule.v6);
            assert!(rule.conds.iter().any(|c| matches!(c, Cond::IcmpType(t) if ND_TYPES.contains(t))), "every ICMPv6 rule names an ND type");
            assert!(rule.conds.iter().any(|c| matches!(c, Cond::RemoteNet(n) if LINK_V6.iter().any(|l| Net::parse(l).unwrap() == *n))), "and a link range");
        }
        assert_eq!(r.iter().filter(|x| x.conds.contains(&Cond::Protocol(ICMPV6))).count(), 5 * 2 * 2);
    }

    #[test]
    fn l11_dhcp_only_to_broadcast_and_the_local_ranges() {
        let r = rules(&plan(true, false));
        let dhcp = |remote: &str| Packet { local_port: 68, ..out(None, UDP, remote, 67) };
        for ok in ["255.255.255.255", "192.168.1.1", "10.0.0.1", "172.16.5.1", "169.254.1.1"] {
            assert!(verdict(&r, &dhcp(ok)), "{ok}");
        }
        for bad in ["8.8.8.8", "203.0.113.5", "1.1.1.1"] {
            assert!(!verdict(&r, &dhcp(bad)), "{bad}: a side channel past the kill switch");
        }
        assert!(!verdict(&r, &out(None, UDP, "192.168.1.1", 67)), "port 67 from another local port isn't DHCP");
        let offer = Packet { dir: Dir::In, local_port: 68, ..out(None, UDP, "203.0.113.1", 67) };
        assert!(verdict(&r, &offer), "a DHCP server's answer, wherever it is");
        assert!(!verdict(&r, &Packet { remote_port: 9999, ..offer }), "something else to port 68");
        for rule in r.iter().filter(|x| x.dir == Dir::Out && x.conds.contains(&Cond::RemotePort(67))) {
            assert!(rule.conds.iter().any(|c| matches!(c, Cond::RemoteNet(_))), "every outgoing DHCP rule names its range");
        }
        let v6 = |remote: &str| Packet { local: ip("fe80::20"), local_port: 546, ..out(None, UDP, remote, 547) };
        assert!(verdict(&r, &v6("ff02::1:2")));
        assert!(verdict(&r, &v6("fe80::1")));
        assert!(!verdict(&r, &v6("2001:db8::547")), "DHCPv6 to the internet");
    }

    #[test]
    fn l12_the_window_reaches_only_https_and_the_tunnel_only_its_server() {
        let r = rules(&plan(true, false));
        assert!(verdict(&r, &out(Some(APP_WINDOW), TCP, "203.0.113.9", 443)), "sign-in, the next server's settings");
        assert!(verdict(&r, &out(Some(APP_WINDOW), TCP, "2001:db8::9", 443)));
        assert!(!verdict(&r, &out(Some(APP_WINDOW), TCP, "203.0.113.9", 80)), "plain HTTP");
        assert!(!verdict(&r, &out(Some(APP_WINDOW), UDP, "203.0.113.9", 443)), "QUIC");
        assert!(!verdict(&r, &out(Some(APP_WINDOW), TCP, "203.0.113.9", 22)));
        assert!(!verdict(&r, &Packet { dir: Dir::In, ..out(Some(APP_WINDOW), TCP, "203.0.113.9", 443) }), "nothing may connect in to it");
        assert!(verdict(&r, &out(Some(APP_SERVICE), UDP, "2.29.149.29", 51821)), "the tunnel's own UDP");
        assert!(verdict(&r, &out(None, UDP, "2.29.149.29", 51821)), "sent by WireGuard's driver, which has no program");
        assert!(verdict(&r, &Packet { dir: Dir::In, ..out(None, UDP, "2.29.149.29", 51821) }), "the server's answers");
        assert!(!verdict(&r, &out(Some(APP_SERVICE), UDP, "2.29.149.30", 51821)), "another address");
        assert!(!verdict(&r, &out(Some(APP_SERVICE), UDP, "2.29.149.29", 51820)), "another port");
        assert!(!verdict(&r, &out(Some(APP_SERVICE), TCP, "2.29.149.29", 443)), "TCP from the service");
        assert!(!verdict(&r, &out(Some(APP_SERVICE), TCP, "1.1.1.1", 443)), "the service fetches updates through the tunnel, not around it");
        assert!(!r.iter().any(|x| x.conds.contains(&Cond::App(APP_SERVICE))), "with the server known, no rule is just 'the service'");
        assert!(r.iter().filter(|x| x.conds.iter().any(|c| matches!(c, Cond::App(_)))).all(|x| x.dir == Dir::Out && x.conds.len() > 1), "no program is allowed everything");
        // a server address that couldn't be resolved: the service's UDP anywhere (the 2.1 behaviour for UDP only)
        let unknown = rules(&Plan { endpoint: None, ..plan(true, false) });
        assert!(verdict(&unknown, &out(Some(APP_SERVICE), UDP, "198.51.100.7", 51821)));
        assert!(!verdict(&unknown, &out(Some(APP_SERVICE), TCP, "198.51.100.7", 443)));
        assert!(!verdict(&unknown, &out(None, UDP, "198.51.100.7", 51821)), "but not any program");
        // an IPv6 server
        let v6 = rules(&Plan { endpoint: Some("[2a01:4f9::1]:51821".parse().unwrap()), ..plan(true, false) });
        assert!(verdict(&v6, &out(None, UDP, "2a01:4f9::1", 51821)));
        assert!(!verdict(&v6, &out(None, UDP, "2.29.149.29", 51821)));
    }

    #[test]
    fn l14_dns_over_tls_blocked_outside_the_tunnel_when_asked() {
        let lan = Plan { block_dot: true, allow: vec![Net::parse("9.9.9.0/24").unwrap()], ..plan(true, true) };
        let r = rules(&lan);
        assert!(!verdict(&r, &out(None, TCP, "192.168.1.1", 853)), "to the router");
        assert!(!verdict(&r, &out(None, TCP, "9.9.9.9", 853)), "to a range kept off the VPN");
        assert!(!verdict(&r, &out(None, TCP, "fe80::1", 853)));
        assert!(verdict(&r, &out(None, TCP, "9.9.9.9", 443)), "the range is otherwise still allowed");
        let inside = Packet { local: ip("10.66.0.5"), ..out(None, TCP, "9.9.9.9", 853) };
        assert!(verdict(&r, &inside), "DNS over TLS inside the tunnel is fine");
        let off = rules(&Plan { block_dot: false, ..lan.clone() });
        assert!(verdict(&off, &out(None, TCP, "9.9.9.9", 853)), "off by default");
        let dot_rules: Vec<_> = r.iter().filter(|x| x.conds.contains(&Cond::RemotePort(DOT_PORT))).collect();
        assert_eq!(dot_rules.len(), 2);
        assert!(dot_rules.iter().all(|x| !x.permit && x.weight > W_ALLOWED && x.weight < W_TUNNEL));
    }

    #[test]
    fn every_rule_s_ranges_match_its_family() {
        for p in [plan(true, true), Plan { endpoint: Some("[2a01:4f9::1]:51821".parse().unwrap()), block_dot: true, ..plan(true, true) }] {
            for r in rules(&p) {
                for c in &r.conds {
                    if let Cond::LocalNet(n) | Cond::RemoteNet(n) = c {
                        assert_eq!(n.v6(), r.v6, "{}", r.name);
                    }
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
    fn m3_strict_needs_the_kill_switch_and_stays_out_of_an_old_service_s_way() {
        assert!(Protect { strict: true, kill_switch: false, ..Default::default() }.check().is_err());
        assert!(Protect { strict: true, kill_switch: true, ..Default::default() }.check().is_ok());
        assert_eq!(serde_json::to_string(&Protect { kill_switch: true, ..Default::default() }).unwrap(), r#"{"kill_switch":true,"allow_lan":false,"allow":[]}"#, "a 2.1 service refuses unknown fields: off means absent");
        let on: Protect = serde_json::from_str(r#"{"kill_switch":true,"strict":true,"block_dot":true}"#).unwrap();
        assert!(on.strict && on.block_dot);
        assert!(serde_json::to_string(&on).unwrap().contains(r#""strict":true"#));
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
