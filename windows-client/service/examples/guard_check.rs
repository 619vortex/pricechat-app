//! The kill switch and DNS guard on a real Windows machine (a GitHub Actions runner, as administrator), with no VPN account needed:
//! puts the real filters in place, checks what gets through and what doesn't, and that the block disappears the moment the process
//! holding it dies. Every block lasts seconds, so the runner keeps its own connection.
//!   guard_check run <report.json>
//!   guard_check service <report.json>   (the installed service, with a test server that never answers)
//!   guard_check probe <ip> <port>     (exit 0 if a TCP connection opens within 3 s)
//!   guard_check hold <seconds>        (holds a kill switch, prints "held", then exits)
//!   guard_check cut <ip> <seconds>    (cuts one address off, both ways, prints "held"; the failover test)
//!   guard_check hold-strict           (puts the strict kill switch's persistent filters in place, prints "held", exits)
//!   guard_check unblock-strict        (removes them)

#[cfg(windows)]
mod check {
    use kiwivpn::firewall::Guard;
    use kiwivpn::guard::{Net, Plan};
    use std::io::{BufRead, BufReader};
    use std::net::{SocketAddr, TcpListener, TcpStream, UdpSocket};
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    struct Report(Vec<serde_json::Value>);

    impl Report {
        fn add(&mut self, name: &str, result: &str, detail: impl Into<String>) {
            let detail = detail.into();
            println!("{result:<4} {name}: {detail}");
            self.0.push(serde_json::json!({ "name": name, "result": result, "detail": detail }));
        }
        fn check(&mut self, name: &str, ok: bool, detail: impl Into<String>) -> bool {
            self.add(name, if ok { "PASS" } else { "FAIL" }, detail);
            ok
        }
    }

    fn tcp(addr: &str) -> bool {
        addr.parse::<SocketAddr>().ok().is_some_and(|a| TcpStream::connect_timeout(&a, Duration::from_secs(3)).is_ok())
    }

    /// One DNS question for example.com to `server`, true if any answer comes back.
    fn dns(server: &str) -> bool {
        let Ok(sock) = UdpSocket::bind(if server.starts_with('[') { "[::]:0" } else { "0.0.0.0:0" }) else { return false };
        let _ = sock.set_read_timeout(Some(Duration::from_secs(3)));
        let mut q = vec![0x4b, 0x69, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0];
        for label in ["example", "com"] {
            q.push(label.len() as u8);
            q.extend_from_slice(label.as_bytes());
        }
        q.extend_from_slice(&[0, 0, 1, 0, 1]);
        if sock.send_to(&q, server).is_err() {
            return false;
        }
        let mut buf = [0u8; 512];
        matches!(sock.recv_from(&mut buf), Ok((n, _)) if n > 12 && buf[0] == 0x4b && buf[1] == 0x69)
    }

    fn probe_from(exe: &Path, addr: &str) -> bool {
        let (ip, port) = addr.rsplit_once(':').unwrap_or((addr, "443"));
        Command::new(exe).args(["probe", ip, port]).stdout(Stdio::null()).stderr(Stdio::null()).status().is_ok_and(|s| s.success())
    }

    fn loopback() -> bool {
        let Ok(l) = TcpListener::bind("127.0.0.1:0") else { return false };
        let Ok(addr) = l.local_addr() else { return false };
        TcpStream::connect_timeout(&addr, Duration::from_secs(3)).is_ok()
    }

    fn plan(kill: bool) -> Plan {
        Plan {
            kill_switch: kill,
            allow_lan: true,
            tunnel: vec![Net::parse("10.255.255.2/32").unwrap(), Net::parse("fd00:6b69::2/128").unwrap()],
            allow: vec![Net::parse("1.0.0.1/32").unwrap()],
            apps: 2,
            endpoint: Some("192.0.2.1:51820".parse().unwrap()),
            block_dot: false,
        }
    }

    /// The service's place is a program that doesn't exist (its rules are left out); the window's is the copied probe.
    fn apps(window: &Path) -> [&Path; 2] {
        [Path::new(r"C:\nonexistent\kiwivpn-service.exe"), window]
    }

    const CF: &str = "1.1.1.1:443";
    const CF_KEPT_OFF: &str = "1.0.0.1:443";
    const CF6: &str = "[2606:4700:4700::1111]:443";
    const DNS4: &str = "8.8.8.8:53";
    const CF_HTTP: &str = "1.1.1.1:80";
    const CF_KEPT_OFF_DOT: &str = "1.0.0.1:853";

    pub fn run(report_path: &str) -> i32 {
        let mut r = Report(Vec::new());
        let me = std::env::current_exe().expect("own path");
        let allowed: PathBuf = me.with_file_name("allowed-probe.exe");
        let _ = std::fs::copy(&me, &allowed);

        // before anything
        let base_tcp = tcp(CF);
        let base_dns = dns(DNS4);
        let base_v6 = tcp(CF6);
        r.check("baseline: this machine reaches the internet", base_tcp && base_dns, format!("TCP {CF} {base_tcp}, DNS {DNS4} {base_dns}, IPv6 {base_v6}"));
        if !(base_tcp && base_dns) {
            return finish(r, report_path);
        }

        // DNS guard alone (what every connection gets, kill switch or not)
        match Guard::engage(&plan(false), &apps(&allowed)) {
            Ok(g) => {
                let (t, d, lo) = (tcp(CF), dns(DNS4), loopback());
                r.check("DNS guard: ordinary traffic still flows", t && lo, format!("TCP {t}, loopback {lo}"));
                r.check("DNS guard: DNS outside the tunnel is blocked", !d, format!("DNS to {DNS4} answered: {d}, {} filters", g.filters()));
                drop(g);
                let d2 = dns(DNS4);
                r.check("DNS guard: released, DNS works again", d2, format!("DNS {d2}"));
            }
            Err(e) => {
                r.check("DNS guard: turns on", false, e);
            }
        }

        // the kill switch
        let base_http = tcp(CF_HTTP);
        let base_dot = tcp(CF_KEPT_OFF_DOT);
        match Guard::engage(&plan(true), &apps(&allowed)) {
            Ok(g) => {
                let started = Instant::now();
                let (t, d, lo) = (tcp(CF), dns(DNS4), loopback());
                r.check("kill switch: IPv4 outside the VPN is blocked", !t, format!("TCP {CF} opened: {t}, {} filters", g.filters()));
                r.check("kill switch: DNS outside the VPN is blocked", !d, format!("DNS {DNS4} answered: {d}"));
                if base_v6 {
                    let v6 = tcp(CF6);
                    r.check("kill switch: IPv6 outside the VPN is blocked", !v6, format!("TCP {CF6} opened: {v6}"));
                } else {
                    r.add("kill switch: IPv6 outside the VPN is blocked", "WARN", "this runner has no IPv6, so nothing to block (the IPv6 rules are in place)");
                }
                r.check("kill switch: loopback still works", lo, format!("{lo}"));
                let kept = tcp(CF_KEPT_OFF);
                r.check("kill switch: a split-tunnel range still goes through", kept, format!("TCP {CF_KEPT_OFF} opened: {kept}"));
                let app = probe_from(&allowed, CF);
                r.check("kill switch: Kiwi's window still gets through over HTTPS (to reconnect)", app, format!("allowed program reached {CF}: {app}"));
                if base_http {
                    let http = probe_from(&allowed, CF_HTTP);
                    r.check("kill switch (L12): Kiwi's window reaches HTTPS only, not port 80", !http, format!("allowed program reached {CF_HTTP}: {http}"));
                } else {
                    r.add("kill switch (L12): Kiwi's window reaches HTTPS only, not port 80", "WARN", format!("{CF_HTTP} isn't reachable from this runner at all"));
                }
                let other = probe_from(&me, CF);
                r.check("kill switch: any other program is blocked", !other, format!("another program reached {CF}: {other}"));
                drop(g);
                r.add("kill switch: time blocked", "INFO", format!("{:.1} s", started.elapsed().as_secs_f32()));
                let (t2, d2) = (tcp(CF), dns(DNS4));
                r.check("kill switch: turned off, the internet is back at once", t2 && d2, format!("TCP {t2}, DNS {d2}"));
            }
            Err(e) => {
                r.check("kill switch: turns on", false, e);
            }
        }

        // switching servers: the new rules go in while the old ones still hold, and the block never lapses in between
        // L14: DNS over TLS is blocked outside the tunnel when asked, even to a range kept off the VPN
        match Guard::engage(&Plan { block_dot: true, ..plan(true) }, &apps(&allowed)) {
            Ok(g) => {
                let (dot, https) = (tcp(CF_KEPT_OFF_DOT), tcp(CF_KEPT_OFF));
                if base_dot {
                    r.check("kill switch (L14): DNS over TLS outside the tunnel is blocked", !dot && https, format!("{CF_KEPT_OFF_DOT} opened: {dot}; {CF_KEPT_OFF} still opens: {https}; {} filters", g.filters()));
                } else {
                    r.add("kill switch (L14): DNS over TLS outside the tunnel is blocked", "WARN", format!("{CF_KEPT_OFF_DOT} isn't reachable from this runner at all"));
                }
            }
            Err(e) => {
                r.check("kill switch (L14): turns on", false, e);
            }
        }

        match (Guard::engage(&plan(true), &apps(&allowed)), Guard::engage(&plan(true), &apps(&allowed))) {
            (Ok(old), Ok(new)) => {
                drop(old);
                let still = !tcp(CF);
                r.check("kill switch: replaced while on (switching servers), never lapses", still, format!("still blocked with only the new rules: {still}"));
                drop(new);
                let back = tcp(CF);
                r.check("kill switch: the replacement turns off cleanly", back, format!("TCP {back}"));
            }
            (a, b) => {
                let why = [a.err(), b.err()].into_iter().flatten().collect::<Vec<_>>().join("; ");
                r.check("kill switch: replaced while on (switching servers), never lapses", false, why);
            }
        }

        // the safeguard: the block dies with the process that holds it, however it dies
        match Command::new(&me).args(["hold", "30"]).stdout(Stdio::piped()).spawn() {
            Ok(mut child) => {
                let mut line = String::new();
                let held = child.stdout.take().map(|out| BufReader::new(out).read_line(&mut line).is_ok() && line.trim() == "held").unwrap_or(false);
                let blocked = held && !tcp(CF);
                r.check("safeguard: another process's kill switch blocks", blocked, format!("held: {held}, blocked: {blocked}"));
                let _ = child.kill(); // TerminateProcess: no cleanup code of ours runs
                let _ = child.wait();
                let t0 = Instant::now();
                let mut back = false;
                while t0.elapsed() < Duration::from_secs(10) {
                    if tcp(CF) && dns(DNS4) {
                        back = true;
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(250));
                }
                r.check("safeguard: killing that process gives the internet back by itself", back, format!("after {:.1} s", t0.elapsed().as_secs_f32()));
            }
            Err(e) => {
                r.check("safeguard: holder starts", false, e.to_string());
            }
        }
        // M3, strict: persistent filters outlive the process that put them in place, and only removing them gives the internet back
        let _ = kiwivpn::firewall::strict_remove();
        match Command::new(&me).arg("hold-strict").output() {
            Ok(out) => {
                let said = String::from_utf8_lossy(&out.stdout).to_string();
                let held = said.lines().next() == Some("held");
                let blocked = held && !tcp(CF) && !dns(DNS4);
                let present = kiwivpn::firewall::strict_present();
                r.check("strict (M3): the block holds after its process has exited", blocked && present, format!("held: {held}, blocked: {blocked}, filters present: {present}; {}", said.trim().replace('\n', " / ")));
                let app = probe_from(&allowed, CF);
                r.check("strict (M3): Kiwi's window still gets through over HTTPS", app, format!("{app}"));
                let removed = kiwivpn::firewall::strict_remove();
                let t0 = Instant::now();
                let back = until(Duration::from_secs(10), || tcp(CF) && dns(DNS4));
                r.check("strict (M3): removing it gives the internet back at once", removed.is_ok() && back.is_some() && !kiwivpn::firewall::strict_present(),
                        format!("removed: {removed:?}, back after {:.1} s", t0.elapsed().as_secs_f32()));
            }
            Err(e) => {
                r.check("strict (M3): holder starts", false, e.to_string());
            }
        }
        let _ = kiwivpn::firewall::strict_remove();
        let _ = std::fs::remove_file(&allowed);
        finish(r, report_path)
    }

    /// Puts the strict filters in place and exits without removing them (the parent checks they outlive this process).
    pub fn hold_strict() -> i32 {
        let me = std::env::current_exe().expect("own path");
        let allowed = me.with_file_name("allowed-probe.exe");
        match kiwivpn::firewall::strict_engage(&plan(true), &apps(&allowed)) {
            Ok((n, boot)) => {
                println!("held");
                println!("{n} persistent filters, boot-time: {boot:?}");
                0
            }
            Err(e) => {
                println!("failed: {e}");
                1
            }
        }
    }

    fn finish(r: Report, path: &str) -> i32 {
        let failed = r.0.iter().filter(|c| c["result"] == "FAIL").count();
        let _ = std::fs::write(path, serde_json::to_string_pretty(&r.0).unwrap_or_default());
        println!("\n{} checks, {failed} failed", r.0.len());
        i32::from(failed > 0)
    }

    // --- the installed service ------------------------------------------------------------------------------------------

    use kiwivpn::protocol::{Protect, Request, State, Status, PAUSE_LIMIT_SECONDS};

    /// A well-formed config for a server that doesn't exist (TEST-NET-1), so the tunnel runs and never gets an answer.
    const NOWHERE: &str = "[Interface]\nPrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=\nAddress = 10.255.255.2/32\nDNS = 10.255.255.1\n\n[Peer]\nPublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=\nEndpoint = 192.0.2.1:51820\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n";

    fn ask(r: &Request) -> Result<Status, String> {
        kiwivpn::windows::ask_service(r).map_err(|e| e.message())
    }

    fn connect(kill: bool) -> Result<Status, String> {
        ask(&Request::Connect { config: NOWHERE.into(), protect: Protect { kill_switch: kill, allow_lan: true, allow: Vec::new(), ..Default::default() } })
    }

    fn ps(script: &str) -> String {
        Command::new("powershell.exe").args(["-NoProfile", "-Command", script]).output().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default()
    }

    fn kill_service_process(name: &str) -> bool {
        let name = name.replace('$', "`$");
        ps(&format!("$p = (Get-CimInstance Win32_Service -Filter \"Name='{name}'\").ProcessId; if ($p) {{ Stop-Process -Id $p -Force; 'killed' }}")).contains("killed")
    }

    /// Waits until `f` holds, checking every half second.
    fn until(limit: Duration, f: impl Fn() -> bool) -> Option<f32> {
        let t0 = Instant::now();
        while t0.elapsed() < limit {
            if f() {
                return Some(t0.elapsed().as_secs_f32());
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        None
    }

    fn online() -> bool {
        tcp(CF) && dns(DNS4)
    }

    fn secs(t: Option<f32>) -> String {
        t.map(|t| format!("{t:.0} s")).unwrap_or_else(|| "never".into())
    }

    pub fn service(report_path: &str) -> i32 {
        let mut r = Report(Vec::new());
        let st = ask(&Request::Status);
        let features = st.as_ref().map(|s| s.features.join(", ")).unwrap_or_default();
        let enforces = st.as_ref().is_ok_and(|s| s.features.iter().any(|f| f == "kill_switch"));
        if !r.check("service: installed, answers, and says it enforces the kill switch", enforces, format!("{:?}; features: {features}", st.as_ref().map(|s| s.state))) {
            return finish(r, report_path);
        }
        let _ = ask(&Request::Disconnect);
        r.check("service: online before the tests", until(Duration::from_secs(20), online).is_some(), "");

        // 1. the tunnel crashes under the kill switch: blocked meanwhile, started again by the service
        match connect(true) {
            Ok(s) => {
                r.check("service: connect with the kill switch on", s.kill_switch && s.state != State::Failed, format!("{:?}, blocking {}, error {:?}", s.state, s.kill_switch, s.error));
                std::thread::sleep(Duration::from_secs(3));
                let killed = kill_service_process("WireGuardTunnel$kiwi");
                std::thread::sleep(Duration::from_secs(1));
                // with the tunnel's process gone its routes are gone too: only the kill switch stands between this PC and the internet now
                let leak = online();
                r.check("service: tunnel crashed, still nothing gets out", killed && !leak, format!("tunnel process killed: {killed}, reached the internet: {leak}"));
                let restarted = until(Duration::from_secs(40), || ps("(Get-Service 'WireGuardTunnel$kiwi').Status") == "Running");
                r.check("service: brings the crashed tunnel back by itself", restarted.is_some(), format!("running again after {}", secs(restarted)));
                // 2. a new connection that never reaches its server is given up after a minute, and the block goes with it
                let freed = until(Duration::from_secs(120), || ask(&Request::Status).is_ok_and(|s| s.state == State::Failed && !s.kill_switch));
                let back = until(Duration::from_secs(15), online);
                let msg = ask(&Request::Status).ok().and_then(|s| s.error).unwrap_or_default();
                r.check("service: a server that never answers doesn't leave the PC offline", freed.is_some() && back.is_some(), format!("given up after {}, internet back: {}; says: {msg}", secs(freed), back.is_some()));
            }
            Err(e) => {
                r.check("service: connect with the kill switch on", false, e);
            }
        }

        // 3. switching servers keeps the block; Disconnect releases it at once
        if connect(true).is_ok() {
            let _ = ask(&Request::Pause); // no tunnel routes from here: a block now is the kill switch's alone
            let blocked = !online();
            let still = ask(&Request::Status).is_ok_and(|s| s.kill_switch);
            r.check("service: switching servers, everything still blocked", blocked && still, format!("blocked: {blocked}, service says blocking: {still}"));
            let _ = ask(&Request::Disconnect);
            let back = until(Duration::from_secs(10), online);
            r.check("service: Disconnect gives the internet back at once", back.is_some(), format!("after {}", secs(back)));
        }

        // 4. a pause with no connect after it lets go by itself
        if connect(true).is_ok() {
            let _ = ask(&Request::Pause);
            let held = !online();
            let freed = until(Duration::from_secs(PAUSE_LIMIT_SECONDS + 30), online);
            r.check("service: a pause with nothing after it lets go by itself", held && freed.is_some(), format!("blocked at first: {held}, free after {} (limit {PAUSE_LIMIT_SECONDS} s)", secs(freed)));
            let _ = ask(&Request::Disconnect);
        }

        // 5. without the kill switch nothing is blocked once the tunnel is closed
        if let Ok(s) = connect(false) {
            r.check("service: kill switch off means it isn't blocking", !s.kill_switch, format!("blocking {}", s.kill_switch));
            let _ = ask(&Request::Pause);
            let back = until(Duration::from_secs(10), online);
            r.check("service: no kill switch, closing the tunnel restores DNS and traffic", back.is_some(), format!("after {}", secs(back)));
            let _ = ask(&Request::Disconnect);
        }

        // 6. the service itself crashes: Windows starts it again, and the wanted tunnel comes back with its kill switch
        if connect(true).is_ok() {
            std::thread::sleep(Duration::from_secs(2));
            let killed = kill_service_process("KiwiVPN");
            let back = until(Duration::from_secs(60), || ask(&Request::Status).is_ok_and(|s| s.kill_switch));
            r.check("service: crashed, Windows restarts it and the kill switch comes back", killed && back.is_some(), format!("killed: {killed}, blocking again after {}", secs(back)));
            let _ = ask(&Request::Disconnect);
            let free = until(Duration::from_secs(15), online);
            r.check("service: and Disconnect still releases it", free.is_some(), format!("after {}", secs(free)));
        }
        let _ = ask(&Request::Disconnect);

        // 7. the strict kill switch (2.2, M3): holds through a give-up and while the service is stopped; only Disconnect or
        //    `kiwivpn-service unblock` lift it
        let strict = |s: &Status| s.strict && s.kill_switch;
        let strict_connect = || ask(&Request::Connect { config: NOWHERE.into(), protect: Protect { kill_switch: true, allow_lan: true, strict: true, ..Default::default() } });
        match strict_connect() {
            Ok(s) => {
                r.check("strict: connect with the strict kill switch on", strict(&s) && s.state != State::Failed, format!("{:?}, strict {}, error {:?}", s.state, s.strict, s.error));
                let gave_up = until(Duration::from_secs(120), || ask(&Request::Status).is_ok_and(|s| s.state == State::Failed));
                let after = ask(&Request::Status);
                let leak = online();
                r.check("strict: a server that never answers is given up, and the block stays", gave_up.is_some() && after.as_ref().is_ok_and(strict) && !leak,
                        format!("given up after {}, still strict: {}, reached the internet: {leak}; says: {}", secs(gave_up), after.as_ref().is_ok_and(strict), after.as_ref().ok().and_then(|s| s.error.clone()).unwrap_or_default()));
                let _ = ask(&Request::Disconnect);
                let back = until(Duration::from_secs(15), online);
                let lifted = ask(&Request::Status).is_ok_and(|s| !s.strict && !s.kill_switch);
                r.check("strict: Disconnect lifts it", back.is_some() && lifted, format!("internet back after {}, lifted: {lifted}", secs(back)));
            }
            Err(e) => {
                r.check("strict: connect with the strict kill switch on", false, e);
            }
        }
        if strict_connect().is_ok() {
            std::thread::sleep(Duration::from_secs(2));
            let stopped = ps("Stop-Service KiwiVPN -Force; Stop-Service 'WireGuardTunnel$kiwi' -Force -ErrorAction SilentlyContinue; (Get-Service KiwiVPN).Status") == "Stopped";
            std::thread::sleep(Duration::from_secs(2));
            let leak = online();
            r.check("strict: service and tunnel stopped (as during an update), still nothing gets out", stopped && !leak, format!("stopped: {stopped}, reached the internet: {leak}"));
            ps("Start-Service KiwiVPN");
            let seen = until(Duration::from_secs(60), || ask(&Request::Status).is_ok_and(|s| s.strict));
            r.check("strict: the service, started again, reports the block", seen.is_some(), format!("after {}", secs(seen)));
            let exe = r"C:\Program Files\KiwiVPN\kiwivpn-service.exe";
            let out = Command::new(exe).arg("unblock").output().map(|o| format!("{} {}", String::from_utf8_lossy(&o.stdout).trim(), String::from_utf8_lossy(&o.stderr).trim())).unwrap_or_else(|e| e.to_string());
            let back = until(Duration::from_secs(15), online);
            let lifted = until(Duration::from_secs(15), || ask(&Request::Status).is_ok_and(|s| !s.strict && !s.kill_switch)).is_some();
            r.check("strict: kiwivpn-service unblock lifts it", back.is_some() && lifted, format!("internet back after {}, lifted: {lifted}; unblock said: {}", secs(back), out.replace('\n', " / ")));
        }
        let _ = ask(&Request::Disconnect);
        let _ = kiwivpn::firewall::strict_remove();
        finish(r, report_path)
    }

    pub fn probe(ip: &str, port: &str) -> i32 {
        let addr = if ip.contains(':') { format!("[{ip}]:{port}") } else { format!("{ip}:{port}") };
        i32::from(!tcp(&addr))
    }

    /// Cuts `ip` off completely (both directions, every protocol) until `seconds` pass or this process is killed: the VPN server
    /// "goes dark" for the failover test. A block in its own sublayer beats the permits of any other, Kiwi's included. At the IP
    /// packet layers, so the tunnel's UDP flow, already open, is cut as well (the ALE layers judge a flow only when it opens).
    pub fn cut(ip: &str, seconds: u64) -> i32 {
        use kiwivpn::guard::{Cond, Dir, Rule};
        let Ok(net) = Net::parse(&format!("{ip}/{}", if ip.contains(':') { 128 } else { 32 })) else {
            eprintln!("not an address: {ip}");
            return 2;
        };
        let rules = [Dir::Out, Dir::In].into_iter().map(|dir| Rule { v6: net.v6(), dir, permit: false, weight: 15, conds: vec![Cond::RemoteNet(net)], name: "test: server cut off" }).collect();
        match Guard::engage_rules(rules, "Kiwi CI: server cut off", true) {
            Ok(_g) => {
                println!("held");
                std::thread::sleep(Duration::from_secs(seconds.min(1200)));
                0
            }
            Err(e) => {
                eprintln!("{e}");
                1
            }
        }
    }

    pub fn hold(seconds: u64) -> i32 {
        match Guard::engage(&plan(true), &[]) {
            Ok(_g) => {
                println!("held");
                std::thread::sleep(Duration::from_secs(seconds.min(60)));
                0
            }
            Err(e) => {
                eprintln!("{e}");
                1
            }
        }
    }
}

#[cfg(windows)]
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["run", report] => check::run(report),
        ["service", report] => check::service(report),
        ["probe", ip, port] => check::probe(ip, port),
        ["hold", s] => check::hold(s.parse().unwrap_or(20)),
        ["cut", ip, s] => check::cut(ip, s.parse().unwrap_or(600)),
        ["hold-strict"] => check::hold_strict(),
        ["unblock-strict"] => match kiwivpn::firewall::strict_remove() {
            Ok(n) => {
                println!("removed {n}");
                0
            }
            Err(e) => {
                eprintln!("{e}");
                1
            }
        },
        _ => {
            eprintln!("usage: guard_check run <report.json> | probe <ip> <port> | hold <seconds> | cut <ip> <seconds>");
            2
        }
    };
    std::process::exit(code);
}

#[cfg(not(windows))]
fn main() {}
