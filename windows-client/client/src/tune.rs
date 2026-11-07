//! Kiwi VPN 2.0's connection options, applied to the config Price Chat built: split tunneling (which IPv4 ranges skip the
//! tunnel) and the DNS choice (the server's own resolver, or Quad9's malware-blocking one). The service checks the result
//! again, as it does every config. IPv6 always stays `::/0` inside the tunnel, where it's refused, so it can't leak.

use std::net::Ipv4Addr;
use zeroize::Zeroizing;

/// Quad9's filtering resolvers (malware and phishing blocked), reached through the tunnel.
pub const QUAD9: [&str; 2] = ["9.9.9.9", "149.112.112.112"];
/// The service refuses more allowed ranges than this (service/src/config.rs MAX_ALLOWED).
pub const MAX_ROUTES: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Cidr {
    pub ip: u32,
    pub bits: u8,
}

impl Cidr {
    fn mask(bits: u8) -> u32 {
        if bits == 0 { 0 } else { u32::MAX << (32 - u32::from(bits)) }
    }
    fn overlaps(self, other: Cidr) -> bool {
        let m = Cidr::mask(self.bits.min(other.bits));
        self.ip & m == other.ip & m
    }
    pub fn text(self) -> String {
        format!("{}/{}", Ipv4Addr::from(self.ip), self.bits)
    }
}

pub fn parse_cidr(raw: &str) -> Option<Cidr> {
    let raw = raw.trim();
    let (addr, bits) = match raw.split_once('/') {
        Some((a, b)) => (a, b.parse::<u8>().ok()?),
        None => (raw, 32),
    };
    if bits > 32 {
        return None;
    }
    let ip = u32::from(addr.parse::<Ipv4Addr>().ok()?);
    Some(Cidr { ip: ip & Cidr::mask(bits), bits })
}

/// The person's list, checked again here (the page checked it too). Too-wide ranges and the VPN's own DNS are refused.
pub fn checked_exclusions(lines: &[String], keep_inside: &[Cidr]) -> Result<Vec<Cidr>, String> {
    let mut out = Vec::new();
    for raw in lines.iter().map(|l| l.trim()).filter(|l| !l.is_empty()) {
        let c = parse_cidr(raw).ok_or_else(|| format!("\"{raw}\" isn't an IPv4 address or range"))?;
        if c.bits < 8 {
            return Err(format!("{raw} is too wide to skip the VPN"));
        }
        if keep_inside.iter().any(|k| k.overlaps(c)) {
            return Err(format!("{raw} includes an address that must stay in the VPN"));
        }
        out.push(c);
    }
    Ok(out)
}

fn subtract(block: Cidr, ex: Cidr, out: &mut Vec<Cidr>) {
    if !block.overlaps(ex) {
        out.push(block);
        return;
    }
    if ex.bits <= block.bits {
        return; // all of this block is excluded
    }
    let half = block.bits + 1;
    let lo = Cidr { ip: block.ip, bits: half };
    let hi = Cidr { ip: block.ip + (1u32 << (32 - u32::from(half))), bits: half };
    subtract(lo, ex, out);
    subtract(hi, ex, out);
}

/// 0.0.0.0/0 minus the excluded ranges, as the fewest CIDRs, then `::/0`.
pub fn allowed_excluding(excluded: &[Cidr]) -> Vec<String> {
    let mut blocks = vec![Cidr { ip: 0, bits: 0 }];
    for &ex in excluded {
        let mut next = Vec::new();
        for b in blocks {
            subtract(b, ex, &mut next);
        }
        blocks = next;
    }
    blocks.sort_by_key(|c| (c.ip, c.bits));
    let mut out: Vec<String> = blocks.into_iter().map(Cidr::text).collect();
    out.push("::/0".into());
    out
}

/// The config with the person's options. `exclude` empty and `dns` None leave it exactly as Price Chat made it.
pub fn apply(config: &str, exclude: &[Cidr], dns: Option<&[&str]>) -> Result<Zeroizing<String>, String> {
    let routes = if exclude.is_empty() { None } else { Some(allowed_excluding(exclude)) };
    if let Some(r) = &routes {
        if r.len() > MAX_ROUTES {
            return Err(format!("Those ranges split the connection into {} pieces; Kiwi VPN allows {MAX_ROUTES}. Use fewer or wider ranges.", r.len()));
        }
    }
    let mut out = Zeroizing::new(String::with_capacity(config.len() + 256)); // it holds the private key
    let mut section = "";
    let mut wrote_dns = false;
    for line in config.lines() {
        let t = line.trim();
        let lower = t.to_ascii_lowercase();
        if lower == "[peer]" && section == "interface" && !wrote_dns {
            if let Some(d) = dns {
                out.push_str(&format!("DNS = {}\n", d.join(", ")));
                wrote_dns = true;
            }
        }
        if lower == "[interface]" { section = "interface"; }
        if lower == "[peer]" { section = "peer"; }
        let key = lower.split_once('=').map(|(k, _)| k.trim().to_string()).unwrap_or_default();
        if section == "interface" && key == "dns" {
            if let Some(d) = dns {
                out.push_str(&format!("DNS = {}\n", d.join(", ")));
                wrote_dns = true;
                continue;
            }
        }
        if section == "peer" && key == "allowedips" {
            if let Some(r) = &routes {
                out.push_str(&format!("AllowedIPs = {}\n", r.join(", ")));
                continue;
            }
        }
        out.push_str(line);
        out.push('\n');
    }
    Ok(out)
}

/// The DNS addresses a config uses (to keep them inside the tunnel, and to know which resolvers a leak check should see).
pub fn dns_of(config: &str) -> Vec<String> {
    config.lines().filter_map(|l| {
        let (k, v) = l.split_once('=')?;
        (k.trim().eq_ignore_ascii_case("dns")).then(|| v.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect::<Vec<_>>())
    }).flatten().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const CFG: &str = "[Interface]\nPrivateKey = K\nAddress = 10.66.0.5/32\nDNS = 10.66.0.1\n\n[Peer]\nPublicKey = S\nEndpoint = 178.156.252.184:51820\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n";

    #[test]
    fn nothing_chosen_changes_nothing() {
        assert_eq!(apply(CFG, &[], None).unwrap().as_str(), CFG);
    }

    #[test]
    fn excluded_ranges_leave_the_tunnel_and_ipv6_stays_inside() {
        let ex = checked_exclusions(&["192.168.1.0/24".into()], &[]).unwrap();
        let out = apply(CFG, &ex, None).unwrap();
        let line = out.lines().find(|l| l.starts_with("AllowedIPs")).unwrap();
        assert!(!line.contains("192.168.1.0/24") && line.contains("0.0.0.0/1") && line.ends_with("::/0"));
        let routes = allowed_excluding(&ex);
        let size: u64 = routes[..routes.len() - 1].iter().map(|r| 1u64 << (32 - r.split('/').nth(1).unwrap().parse::<u64>().unwrap())).sum();
        assert_eq!(size, (1u64 << 32) - 256);
        kiwivpn::config::parse(&out).expect("the service accepts it");
    }

    #[test]
    fn dns_is_replaced_or_added_in_the_interface() {
        let out = apply(CFG, &[], Some(&QUAD9)).unwrap();
        assert!(out.contains("DNS = 9.9.9.9, 149.112.112.112\n") && !out.contains("10.66.0.1"));
        let no_dns = CFG.replace("DNS = 10.66.0.1\n", "");
        let out = apply(&no_dns, &[], Some(&QUAD9)).unwrap();
        assert!(out.find("DNS = 9.9.9.9").unwrap() < out.find("[Peer]").unwrap());
        assert_eq!(dns_of(CFG), vec!["10.66.0.1"]);
    }

    #[test]
    fn refuses_what_would_leak_or_break() {
        let keep = [parse_cidr("10.66.0.1").unwrap()];
        assert!(checked_exclusions(&["0.0.0.0/0".into()], &keep).is_err());
        assert!(checked_exclusions(&["10.66.0.0/16".into()], &keep).is_err());
        assert!(checked_exclusions(&["nonsense".into()], &keep).is_err());
        let many: Vec<String> = (0..4).map(|i| format!("{}.1.2.3/32", 20 + i * 40)).collect();
        let ex = checked_exclusions(&many, &keep).unwrap();
        assert!(apply(CFG, &ex, None).unwrap_err().contains("pieces"));
    }
}
