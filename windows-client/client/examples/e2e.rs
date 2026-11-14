//! Kiwi VPN 2.0 end-to-end tests on a real Windows machine (a GitHub Actions runner): the installed service, real
//! WireGuard tunnels to the real Kiwi servers, and the app's own code for devices, split tunneling, DNS and measuring.
//! Nothing here is a stand-in: every connect goes through the installed kiwivpn-service, as the app's do.
//!
//!   e2e run <report.json>   every test below; exits 1 if any fails
//!   e2e seed                only saves the session into the app's store, for the window tests
//!   e2e cleanup             disconnects and deletes this machine's devices from the account
//!
//! The session comes from the env var KIWI_CI_SESSION (a desktop session: VPN API only). It, the device keys and the
//! configs are never printed. Runs as administrator: the service accepts requests from elevated callers.

use kiwiclient::api::{Backend, Http, Server, BASE};
use kiwiclient::flow::{self, Vault};
use kiwiclient::store::FileVault;
use kiwiclient::{measure, tune};
use kiwivpn::protocol::{Protect, Request, State, Status};
use serde::Serialize;
use std::net::ToSocketAddrs;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[derive(Serialize)]
struct Check {
    name: String,
    result: &'static str, // PASS | FAIL | WARN | SKIP
    detail: String,
}

#[derive(Default)]
struct Report {
    checks: Vec<Check>,
}

impl Report {
    fn add(&mut self, name: &str, result: &'static str, detail: impl Into<String>) {
        let detail = detail.into();
        println!("{result:4} {name}: {detail}");
        self.checks.push(Check { name: name.into(), result, detail });
    }
    fn check(&mut self, name: &str, ok: bool, detail: impl Into<String>) -> bool {
        self.add(name, if ok { "PASS" } else { "FAIL" }, detail);
        ok
    }
    fn failed(&self) -> usize {
        self.checks.iter().filter(|c| c.result == "FAIL").count()
    }
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn computer() -> String {
    std::env::var("COMPUTERNAME").unwrap_or_default()
}

fn ask(r: &Request) -> Result<Status, String> {
    kiwivpn::windows::ask_service(r).map_err(|e| e.message())
}

fn vault() -> Result<FileVault, String> {
    let v = FileVault::open()?;
    if v.session().is_none() {
        let s = std::env::var("KIWI_CI_SESSION").map_err(|_| "KIWI_CI_SESSION isn't set".to_string())?;
        if !s.starts_with("kd.") {
            return Err("KIWI_CI_SESSION isn't a Kiwi VPN app session".into());
        }
        v.save_session(s.trim())?;
    }
    if v.refreshed_at().is_none() {
        v.save_refreshed_at(now()); // starts the weekly clock, so neither this nor the window swaps the CI session
    }
    Ok(v)
}

fn agent(timeout: Duration) -> ureq::Agent {
    let tls = native_tls::TlsConnector::new().expect("TLS");
    ureq::AgentBuilder::new().tls_connector(Arc::new(tls)).timeout_connect(Duration::from_secs(5)).timeout(timeout).redirects(0).build()
}

fn get_text(url: &str) -> Option<String> {
    agent(Duration::from_secs(8)).get(url).call().ok()?.into_string().ok().map(|s| s.trim().to_string())
}

fn public_v4() -> Option<String> {
    get_text("https://api.ipify.org")
}

/// The public IPv4 once traffic flows through the tunnel: the first requests after a handshake can still be finding their way
/// (Windows settling its DNS on the new interface). Waits up to 20 s for an address that isn't `home`; returns it and how long it took.
fn vpn_ip(home: &Option<String>) -> (Option<String>, u64) {
    let t0 = Instant::now();
    let mut last = None;
    while t0.elapsed() < Duration::from_secs(20) {
        last = public_v4();
        if last.is_some() && last != *home {
            break;
        }
        std::thread::sleep(Duration::from_millis(1000));
    }
    (last, t0.elapsed().as_secs())
}

fn public_v6() -> Option<String> {
    get_text("https://api6.ipify.org")
}

/// Cloudflare's trace at a fixed address: which IPv4 a request to exactly that address leaves from.
fn trace_ip(addr: &str) -> Option<String> {
    let body = get_text(&format!("https://{addr}/cdn-cgi/trace"))?;
    body.lines().find_map(|l| l.strip_prefix("ip=")).map(str::to_string)
}

/// Connects with this config through the service and waits for the first handshake.
fn connect(config: &str) -> Result<Status, String> {
    connect_with(config, Protect::default())
}

fn connect_with(config: &str, protect: Protect) -> Result<Status, String> {
    let first = ask(&Request::Connect { config: config.to_string(), protect })?;
    if first.state == State::Failed {
        return Err(first.error.unwrap_or_else(|| "the service refused the config".into()));
    }
    let started = Instant::now();
    loop {
        let s = ask(&Request::Status)?;
        if s.state == State::Connected {
            std::thread::sleep(Duration::from_secs(2)); // routes and DNS settle
            return Ok(s);
        }
        if s.state == State::Failed {
            return Err(s.error.unwrap_or_else(|| "the tunnel failed".into()));
        }
        if started.elapsed() > Duration::from_secs(30) {
            return Err(format!("no handshake in 30 s (state {:?})", s.state));
        }
        std::thread::sleep(Duration::from_millis(700));
    }
}

fn disconnect() -> Result<(), String> {
    ask(&Request::Disconnect)?;
    let started = Instant::now();
    while ask(&Request::Status)?.state != State::Disconnected {
        if started.elapsed() > Duration::from_secs(20) {
            return Err("still not disconnected after 20 s".into());
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    std::thread::sleep(Duration::from_secs(2));
    Ok(())
}

fn endpoint_ip(config: &str) -> Option<String> {
    let ep = config.lines().find_map(|l| l.trim().strip_prefix("Endpoint = "))?.trim();
    ep.to_socket_addrs().ok()?.find(|a| a.is_ipv4()).map(|a| a.ip().to_string())
}

fn place(s: &Server) -> String {
    format!("{} #{} ({})", s.name, s.id, s.country_code)
}

fn run(out: &str) -> Result<Report, String> {
    let mut r = Report::default();
    let v = vault()?;
    let http = Http::new(BASE)?;
    let token = v.session().ok_or("no session")?;

    // the service is installed and answers
    let st = ask(&Request::Status);
    if !r.check("service answers", st.is_ok(), format!("{:?}", st.as_ref().map(|s| s.state))) {
        return Ok(r);
    }
    if st.map(|s| s.state != State::Disconnected).unwrap_or(false) {
        let _ = disconnect();
    }

    // the machine's own connection, before any tunnel
    let home4 = public_v4();
    let home6 = public_v6();
    r.check("direct IPv4 known", home4.is_some(), home4.clone().unwrap_or_default());
    r.add("direct IPv6", "SKIP", home6.clone().map(|a| format!("this machine has IPv6 ({a})")).unwrap_or_else(|| "this machine has no IPv6, so an IPv6 leak can't happen here; checked anyway".into()));

    // devices left behind by an earlier run on a machine of the same name would hold slots
    let _ = remove_own_devices(&http, &v, &token);
    let account = http.account(&token).map_err(|e| format!("account: {}", e.message))?;
    let servers: Vec<Server> = account.servers.iter().filter(|s| s.online).cloned().collect();
    r.check("account has VPN access", account.eligible, account.reason.clone().unwrap_or_else(|| format!("{} online servers, {} of {} devices used", servers.len(), account.devices.len(), account.max_devices)));
    if !account.eligible || servers.is_empty() {
        return Ok(r);
    }
    // each server needs a device of its own: these tests use one slot at a time (two for the switch), and give every one back
    let free = account.max_devices.saturating_sub(account.devices.len() as i64);
    let names: Vec<String> = account.devices.iter().map(|d| format!("{} (server #{})", d.name, d.server_id)).collect();
    if !r.check("a free device slot for the tests", free >= 1, format!("{free} free of {}; in use: {}", account.max_devices, if names.is_empty() { "none".into() } else { names.join(", ") })) {
        return Ok(r);
    }

    // Smart Connect's timings, as the app takes them (each server's proxy name)
    let hosts: Vec<(i64, String)> = servers.iter().filter_map(|s| s.proxy_host.clone().map(|h| (s.id, h))).collect();
    if hosts.is_empty() {
        r.add("Smart Connect timings", "WARN", "Price Chat doesn't send the servers' proxy names yet (they come with feat/kiwi-monitoring), so Smart Connect orders by load alone");
    } else {
        let timed = measure::probe(&hosts);
        let shown: Vec<String> = timed.iter().map(|(id, ms)| format!("#{id}: {}", ms.map(|m| format!("{m:.0} ms")).unwrap_or("no answer".into()))).collect();
        r.check("Smart Connect timings", timed.iter().any(|(_, ms)| ms.is_some()), shown.join(", "));
    }

    // every country: connect, the address and DNS sites see, IPv6, then disconnect
    // Price Chat allows 5 device changes (new or deleted) per member in 10 minutes, so this run's devices are kept and reused:
    // one per server while there are slots, the switch test between two kept ones, and only when slots run out is one given back.
    let mut first_cfg: Option<(Server, String)> = None;
    let mut kept: Vec<(Server, String)> = Vec::new();
    let mut switch_done = false;
    for s in &servers {
        let name = format!("connect {}", place(s));
        if kept.len() as i64 >= free {
            if let Some(i) = kept.iter().rposition(|(k, _)| first_cfg.as_ref().map(|(f, _)| f.id) != Some(k.id)) {
                let (gone, _) = kept.remove(i);
                give_back(&http, &v, &token, gone.id);
            }
        }
        let cfg = match flow::config_with_retry(&http, &v, &computer(), s.id, &|| std::thread::sleep(Duration::from_millis(1500))) {
            Ok(c) => c,
            Err(e) => {
                r.check(&name, false, format!("no config: {e}"));
                continue;
            }
        };
        let server_ip = endpoint_ip(&cfg);
        match connect(&cfg) {
            Ok(_) => {
                // how long until traffic flows: by address alone (no DNS), then by name (DNS through the tunnel too)
                let t0 = Instant::now();
                let mut by_address = None;
                while t0.elapsed() < Duration::from_secs(20) {
                    if trace_ip("1.1.1.1").is_some_and(|ip| Some(&ip) != home4.as_ref()) {
                        by_address = Some(t0.elapsed().as_secs_f32());
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
                let (v4, first_after) = vpn_ip(&home4);
                r.add(&format!("{name}: traffic flowing"), "INFO", format!("by address after {}, by name {first_after} s later", by_address.map(|t| format!("{t:.1} s")).unwrap_or("never (20 s)".into())));
                let leak = measure::leak_check(server_ip.clone(), &tune::dns_of(&cfg));
                let moved = v4.is_some() && v4 != home4;
                r.check(&name, moved, format!("public IPv4 {} (server {}, direct {})", v4.clone().unwrap_or("none".into()), server_ip.clone().unwrap_or("?".into()), home4.clone().unwrap_or("?".into())));
                if moved && v4 != server_ip {
                    r.add(&format!("{name}: exit address"), "WARN", "sites see a different address from the server's endpoint (a separate exit IP is fine)");
                }
                r.check(&format!("{name}: no IPv6 leak"), public_v6().is_none(), "IPv6 sites must be unreachable while connected");
                match leak {
                    Ok(l) => {
                        let seen = l.dns.join("; ");
                        let leaky = l.dns.is_empty() || home4.as_ref().map(|h| l.dns.iter().any(|d| d.starts_with(h.as_str()))).unwrap_or(false);
                        let ok_dns = !l.dns.is_empty() && l.dns.iter().all(|d| {
                            let low = d.to_lowercase();
                            l.expected_dns.iter().any(|e| low.starts_with(&e.to_lowercase()) || low.contains(&e.to_lowercase()))
                        });
                        if ok_dns {
                            r.check(&format!("{name}: DNS through the VPN"), true, format!("resolvers seen: {seen}"));
                        } else {
                            r.add(&format!("{name}: DNS through the VPN"), if leaky { "FAIL" } else { "WARN" }, format!("resolvers seen: {seen}; expected {:?}", l.expected_dns));
                        }
                    }
                    Err(e) => r.add(&format!("{name}: DNS through the VPN"), "FAIL", format!("leak check failed: {e}")),
                }
                if first_cfg.is_none() {
                    first_cfg = Some((s.clone(), cfg.to_string()));
                }
                kept.push((s.clone(), cfg.to_string()));
            }
            Err(e) => {
                r.check(&name, false, e);
            }
        }
        let off = disconnect();
        let back = public_v4();
        r.check(&format!("disconnect {}", place(s)), off.is_ok() && back == home4, format!("{:?}, public IPv4 now {}", off.err(), back.unwrap_or("none".into())));
        // switching country while connected, between two servers whose devices this run already has
        if !switch_done && kept.len() >= 2 {
            let (a, a_cfg) = &kept[kept.len() - 2];
            let (b, b_cfg) = &kept[kept.len() - 1];
            let switched = connect(a_cfg).is_ok() && connect(b_cfg).is_ok();
            let (v4, _) = vpn_ip(&home4);
            let want = endpoint_ip(b_cfg);
            r.check("switch country while connected", switched && v4.is_some() && v4 != home4 && v4 == want, format!("{} -> {}: public IPv4 {} (server {})", place(a), place(b), v4.unwrap_or("none".into()), want.unwrap_or("?".into())));
            let _ = disconnect();
            switch_done = true;
        }
    }
    if !switch_done {
        r.add("switch country while connected", "WARN", "needs two servers connected in this run");
    }

    let Some((srv, cfg)) = first_cfg else {
        return Ok(r);
    };

    // reconnect three times, then switch servers while connected
    let mut ok = 0;
    for _ in 0..3 {
        let up = connect(&cfg).is_ok() && vpn_ip(&home4).0.map(|a| Some(a) != home4).unwrap_or(false);
        if disconnect().is_ok() && up {
            ok += 1;
        }
    }
    r.check("reconnect 3 times", ok == 3, format!("{ok}/3 connect + disconnect cycles worked on {}", place(&srv)));

    // speed test through the tunnel (Cloudflare, about 25 MB down and 5 MB up)
    if connect(&cfg).is_ok() {
        match measure::speed_test() {
            Ok(s) => {
                let down = s.down_bytes as f64 * 8.0 / 1e6 / (s.down_ms.max(1) as f64 / 1000.0);
                let up = s.up_bytes as f64 * 8.0 / 1e6 / (s.up_ms.max(1) as f64 / 1000.0);
                r.check("speed test through the VPN", s.down_bytes > 1_000_000, format!("ping {} ms, down {down:.0} Mbps, up {up:.0} Mbps", s.ping_ms.map(|p| format!("{p:.0}")).unwrap_or("?".into())));
            }
            Err(e) => {
                r.check("speed test through the VPN", false, e);
            }
        }
        let _ = disconnect();
    }

    // split tunneling: one fixed Cloudflare address skips the VPN, its twin stays inside
    let keep: Vec<tune::Cidr> = tune::dns_of(&cfg).iter().filter_map(|d| tune::parse_cidr(d)).collect();
    let pair = [("1.0.0.1", "1.1.1.1"), ("1.1.1.1", "1.0.0.1")].into_iter().find(|(out, _)| tune::checked_exclusions(&[format!("{out}/32")], &keep).is_ok());
    match pair {
        None => r.add("split tunneling", "SKIP", "both Cloudflare addresses are this server's DNS, so neither may skip the VPN"),
        Some((outside, inside)) => {
            let ex = tune::checked_exclusions(&[format!("{outside}/32")], &keep).unwrap_or_default();
            match tune::apply(&cfg, &ex, None) {
                Err(e) => {
                    r.check("split tunneling", false, e);
                }
                Ok(split) => match connect(&split) {
                    Err(e) => {
                        r.check("split tunneling", false, format!("the service refused the split config: {e}"));
                    }
                    Ok(_) => {
                        let direct = trace_ip(outside);
                        let tunneled = trace_ip(inside);
                        r.check("split tunneling: excluded address skips the VPN", direct.is_some() && direct == home4, format!("{outside} sees {}", direct.unwrap_or("nothing".into())));
                        r.check("split tunneling: everything else stays in the VPN", tunneled.is_some() && tunneled != home4, format!("{inside} sees {}, ipify sees {}", tunneled.unwrap_or("nothing".into()), public_v4().unwrap_or("nothing".into())));
                        r.check("split tunneling: IPv6 still blocked", public_v6().is_none(), "IPv6 stays ::/0 in the tunnel");
                        let _ = disconnect();
                    }
                },
            }
        }
    }

    // malware blocking: Quad9's resolvers, through the tunnel
    match tune::apply(&cfg, &[], Some(&tune::QUAD9)) {
        Err(e) => {
            r.check("malware-blocking DNS", false, e);
        }
        Ok(q) => match connect(&q) {
            Err(e) => {
                r.check("malware-blocking DNS", false, e);
            }
            Ok(_) => {
                match measure::leak_check(endpoint_ip(&q), &tune::dns_of(&q)) {
                    Ok(l) => {
                        let quad9 = l.dns.iter().any(|d| d.to_lowercase().contains("quad9") || d.to_lowercase().contains("woodynet") || d.starts_with("9.9.9.") || d.starts_with("149.112."));
                        r.add("malware-blocking DNS: Quad9 answers", if quad9 { "PASS" } else { "WARN" }, format!("resolvers seen: {}", l.dns.join("; ")));
                    }
                    Err(e) => r.add("malware-blocking DNS: Quad9 answers", "FAIL", e),
                }
                let _ = disconnect();
            }
        },
    }

    kill_switch(&mut r, &cfg, &home4);
    let _ = disconnect();

    // 2.1 Smart Connect without proxy names: every server this run had a config for is timed by its endpoint (TCP to the proxy port)
    let endpoint_ms: Vec<String> = servers.iter().filter_map(|s| v.endpoint(s.id).map(|e| (s, e))).map(|(s, e)| format!("#{} {e}: {}", s.id, measure::time_endpoint(&e).map(|m| format!("{m:.0} ms")).unwrap_or("no answer on 443".into()))).collect();
    r.add("Smart Connect endpoint timings (2.1)", "INFO", if endpoint_ms.is_empty() { "no endpoint seen".into() } else { endpoint_ms.join(", ") });

    // The window's tests (Smart Connect, then a failover) need up to two devices. One device this run made is KEPT, with its key in
    // the app's store (same Windows user): failover prefers a server this PC already has a device for, so the window's switch can
    // land on it without creating one (2.1). The other slot stays free for Smart Connect's pick. The clean-up step removes it after.
    let keep = kept.last().map(|(k, _)| k.id);
    let removed = remove_own_devices_except(&http, &v, &token, keep);
    r.add("test devices given back for the window tests", "INFO", format!("{removed} removed, kept for the window's failover: {}", keep.map(|id| format!("server #{id}")).unwrap_or("none".into())));

    let _ = std::fs::write(out, serde_json::to_string_pretty(&r.checks).unwrap_or_default());
    Ok(r)
}

/// The tunnel service's process, killed the hard way, as a crash would.
fn kill_tunnel_process() -> bool {
    let script = "$p = (Get-CimInstance Win32_Service -Filter \"Name='WireGuardTunnel`$kiwi'\").ProcessId; if ($p) { Stop-Process -Id $p -Force; 'killed' }";
    std::process::Command::new("powershell.exe").args(["-NoProfile", "-Command", script]).output().map(|o| String::from_utf8_lossy(&o.stdout).contains("killed")).unwrap_or(false)
}

/// The kill switch on a real tunnel: on while connected, holding when the tunnel crashes and while it's brought back, released by
/// Disconnect, and letting go by itself when a pause isn't followed by a connect.
fn kill_switch(r: &mut Report, cfg: &str, home4: &Option<String>) {
    let on = Protect { kill_switch: true, allow_lan: true, allow: Vec::new(), ..Default::default() };
    let server = endpoint_ip(cfg);
    match connect_with(cfg, on.clone()) {
        Err(e) => {
            r.check("kill switch: connects with it on", false, e);
            return;
        }
        Ok(s) => {
            let (v4, _) = vpn_ip(home4);
            r.check("kill switch: connects with it on", s.kill_switch && v4.is_some() && v4 != *home4, format!("service says blocking: {}, public IPv4 {}", s.kill_switch, v4.unwrap_or("none".into())));
            r.check("kill switch: no IPv6 while on", public_v6().is_none(), "IPv6 sites unreachable");
        }
    }
    // the tunnel crashes: with the kill switch, nothing may leave directly while the service brings it back
    let killed = kill_tunnel_process();
    let t0 = Instant::now();
    let (mut leaked, mut blocked_seen, mut back_after) = (None, false, None);
    while t0.elapsed() < Duration::from_secs(90) {
        match public_v4() {
            Some(ip) if Some(&ip) == home4.as_ref() => {
                leaked = Some(t0.elapsed().as_secs());
                break;
            }
            Some(_) if t0.elapsed() > Duration::from_secs(3) => {
                back_after = Some(t0.elapsed().as_secs());
                break;
            }
            None => blocked_seen = true,
            _ => {}
        }
        std::thread::sleep(Duration::from_millis(1500));
    }
    r.check("kill switch: tunnel process killed, nothing leaks", killed && leaked.is_none(), format!("killed: {killed}, blocked meanwhile: {blocked_seen}, direct address seen: {}", leaked.map(|s| format!("after {s} s")).unwrap_or("never".into())));
    let state = ask(&Request::Status).map(|s| format!("{:?}, blocking {}", s.state, s.kill_switch)).unwrap_or_default();
    r.check("kill switch: the service brings the tunnel back by itself", back_after.is_some() && leaked.is_none(), format!("back through the VPN after {} (service: {state})", back_after.map(|s| format!("{s} s")).unwrap_or("never in 90 s".into())));
    // Disconnect gives the normal connection back at once
    let off = disconnect();
    let back = public_v4();
    r.check("kill switch: Disconnect releases it", off.is_ok() && back == *home4, format!("public IPv4 {} (direct {})", back.unwrap_or("none".into()), home4.clone().unwrap_or("?".into())));
    // a pause (switching servers) keeps blocking, and lets go by itself if no connect follows
    if connect_with(cfg, on).is_ok() {
        let paused = ask(&Request::Pause).map(|s| s.kill_switch).unwrap_or(false);
        let during = public_v4();
        r.check("kill switch: holds while switching servers", paused && during.is_none(), format!("blocking: {paused}, public IPv4 {}", during.unwrap_or("none (blocked)".into())));
        let t1 = Instant::now();
        let mut free = None;
        while t1.elapsed() < Duration::from_secs(kiwivpn::protocol::PAUSE_LIMIT_SECONDS + 40) {
            if public_v4() == *home4 {
                free = Some(t1.elapsed().as_secs());
                break;
            }
            std::thread::sleep(Duration::from_secs(5));
        }
        r.check("kill switch: a pause with no connect lets go by itself", free.is_some(), format!("internet back after {} (limit {} s)", free.map(|s| format!("{s} s")).unwrap_or("never".into()), kiwivpn::protocol::PAUSE_LIMIT_SECONDS));
        let _ = disconnect();
    }
    let _ = server;
}

/// Deletes this machine's device for one server, and forgets its key.
fn give_back(http: &Http, v: &FileVault, token: &str, server_id: i64) {
    if let Some(saved) = v.device(server_id) {
        let _ = http.delete_device(token, saved.device_id);
        v.forget_device(server_id);
    }
}

/// Deletes every device named after this machine. Returns how many.
fn remove_own_devices(http: &Http, v: &FileVault, token: &str) -> usize {
    remove_own_devices_except(http, v, token, None)
}

/// The same, keeping this machine's device (and its saved key) for one server.
fn remove_own_devices_except(http: &Http, v: &FileVault, token: &str, keep_server: Option<i64>) -> usize {
    let Ok(account) = http.account(token) else { return 0 };
    let me = computer();
    let mut removed = 0;
    let keep_device = keep_server.and_then(|s| v.device_id(s));
    for d in account.devices.iter().filter(|d| !me.is_empty() && d.name.starts_with(&me) && Some(d.id) != keep_device) {
        if http.delete_device(token, d.id).is_ok() {
            v.forget_device(d.server_id);
            removed += 1;
        }
    }
    removed
}

fn remove_named(list: &str) -> Result<(), String> {
    let names: Vec<&str> = list.split(',').map(str::trim).filter(|n| !n.is_empty()).collect();
    if names.is_empty() {
        return Ok(());
    }
    let v = vault()?;
    let http = Http::new(BASE)?;
    let token = v.session().ok_or("no session")?;
    let account = http.account(&token).map_err(|e| e.message)?;
    for name in names {
        match account.devices.iter().find(|d| d.name == name) {
            Some(d) => match http.delete_device(&token, d.id) {
                Ok(()) => println!("removed device \"{name}\""),
                Err(e) => println!("couldn't remove \"{name}\": {}", e.message),
            },
            None => println!("no device named \"{name}\""),
        }
    }
    Ok(())
}

fn cleanup() -> Result<(), String> {
    let _ = disconnect();
    let v = vault()?;
    let http = Http::new(BASE)?;
    let token = v.session().ok_or("no session")?;
    let account = http.account(&token).map_err(|e| e.message)?;
    let me = computer();
    let mut removed = 0;
    for d in account.devices.iter().filter(|d| !me.is_empty() && d.name.starts_with(&me)) {
        if http.delete_device(&token, d.id).is_ok() {
            removed += 1;
        }
    }
    println!("cleanup: disconnected, removed {removed} test device(s) named after {me}");
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let code = match args.get(1).map(String::as_str) {
        Some("run") => match run(args.get(2).map(String::as_str).unwrap_or("e2e-report.json")) {
            Ok(r) => {
                let f = r.failed();
                println!("\n{} checks, {f} failed", r.checks.len());
                if f > 0 { 1 } else { 0 }
            }
            Err(e) => {
                println!("FAIL setup: {e}");
                1
            }
        },
        Some("seed") => match vault() {
            Ok(_) => {
                println!("session saved in the app's store");
                0
            }
            Err(e) => {
                println!("FAIL seed: {e}");
                1
            }
        },
        // the owner's named devices, removed on request (workflow input remove_devices): exact names only, comma-separated
        Some("remove") => match remove_named(args.get(2).map(String::as_str).unwrap_or("")) {
            Ok(()) => 0,
            Err(e) => {
                println!("remove: {e}");
                1
            }
        },
        Some("cleanup") => match cleanup() {
            Ok(()) => 0,
            Err(e) => {
                println!("cleanup: {e}");
                1
            }
        },
        _ => {
            println!("usage: e2e run <report.json> | seed | cleanup");
            2
        }
    };
    std::process::exit(code);
}
