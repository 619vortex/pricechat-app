//! From "choose a server" to a working config, with nothing for the person to do: reuse this PC's device on that server, or make a key and register one.

use crate::api::{Account, ApiError, Backend, Peer, Server};
use crate::keys;
use zeroize::Zeroizing;

pub struct Saved {
    pub device_id: i64,
    pub private_b64: Zeroizing<String>,
}

/// What's kept on this PC. Encrypted in the real one: see `store`.
pub trait Vault {
    fn session(&self) -> Option<Zeroizing<String>>;
    fn save_session(&self, token: &str) -> Result<(), String>;
    fn device(&self, server_id: i64) -> Option<Saved>;
    fn save_device(&self, server_id: i64, device_id: i64, private_b64: &str) -> Result<(), String>;
    fn forget_device(&self, server_id: i64);
    fn last_server(&self) -> Option<i64>;
    fn save_last_server(&self, server_id: i64);
    fn refreshed_at(&self) -> Option<u64>;
    fn save_refreshed_at(&self, seconds: u64);
    fn forget_all(&self);
    /// The device id saved for a server, without opening its key. 2.1: lets failover know which servers need no new device.
    fn device_id(&self, server_id: i64) -> Option<i64> {
        self.device(server_id).map(|d| d.device_id)
    }
    /// A server's WireGuard endpoint host, as its last config gave it (not secret: every config for that server carries it).
    /// Smart Connect times it when the backend gives no proxy name for the server.
    fn endpoint(&self, _server_id: i64) -> Option<String> {
        None
    }
    fn save_endpoint(&self, _server_id: i64, _host: &str) {}
}

pub const SIGNED_OUT: &str = "Please sign in again.";
const FULL: &str = "You've used all your devices. Remove one under My Devices on pricechat.live, then try again.";

/// This PC's name for its device on a server, up to the backend's 32 characters. It's how a reinstalled app finds the device it can no longer use.
pub fn device_name(computer: &str, server: &Server) -> String {
    let host: String = computer.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '-').take(22).collect();
    let host = if host.is_empty() { "Windows PC".to_string() } else { host };
    format!("{host} ({})", server.id)
}

fn friendly(e: ApiError) -> String {
    match e.status {
        401 => SIGNED_OUT.into(),
        409 => FULL.into(),
        429 => "Too many new devices. Please wait a few minutes and try again.".into(),
        _ => e.message,
    }
}

/// The config for the service, rebuilt from the server's answer. The service validates it again, so nothing unexpected reaches SYSTEM either way.
pub fn render_config(private_b64: &str, peer: &Peer) -> Zeroizing<String> {
    let host = if peer.endpoint_host.contains(':') { format!("[{}]", peer.endpoint_host) } else { peer.endpoint_host.clone() };
    let mut out = format!("[Interface]\nPrivateKey = {private_b64}\nAddress = {}\n", peer.addresses.join(", "));
    if !peer.dns.is_empty() {
        out.push_str(&format!("DNS = {}\n", peer.dns.join(", ")));
    }
    out.push_str(&format!("\n[Peer]\nPublicKey = {}\nEndpoint = {host}:{}\nAllowedIPs = {}\n", peer.server_public_key, peer.listen_port, peer.allowed_ips.join(", ")));
    if peer.keepalive > 0 {
        out.push_str(&format!("PersistentKeepalive = {}\n", peer.keepalive));
    }
    Zeroizing::new(out)
}

/// Everything before asking the service to connect. On any failure nothing has changed on this PC's tunnel.
pub fn config_for(backend: &dyn Backend, vault: &dyn Vault, computer: &str, server_id: i64) -> Result<Zeroizing<String>, String> {
    let token = vault.session().ok_or(SIGNED_OUT)?;
    let account: Account = backend.account(&token).map_err(|e| {
        if e.signed_out() {
            vault.forget_all();
        }
        friendly(e)
    })?;
    if !account.eligible {
        return Err(account.reason.unwrap_or_else(|| "Your account doesn't include Kiwi VPN right now.".into()));
    }
    let server = account.servers.iter().find(|s| s.id == server_id).ok_or("That server is no longer available. Please choose another.")?;
    if !server.online {
        return Err("That server is offline right now. Please choose another.".into());
    }
    if let Some(saved) = vault.device(server_id) {
        if account.devices.iter().any(|d| d.id == saved.device_id) {
            let peer = backend.peer(&token, saved.device_id).map_err(friendly)?;
            vault.save_endpoint(server_id, &peer.endpoint_host);
            vault.save_last_server(server_id);
            return Ok(render_config(&saved.private_b64, &peer));
        }
        vault.forget_device(server_id); // removed on the website: this key no longer works
    }
    let name = device_name(computer, server);
    // a device with this PC's name whose key is gone, as after a reinstall: it can never connect again, so it shouldn't hold a slot
    if let Some(stale) = account.devices.iter().find(|d| d.server_id == server_id && d.name == name) {
        backend.delete_device(&token, stale.id).map_err(friendly)?;
    }
    let pair = keys::generate()?;
    let added = backend.add_device(&token, &name, server_id, &pair.public_b64).map_err(friendly)?;
    vault.save_device(server_id, added.device.id, &pair.private_b64)?;
    vault.save_endpoint(server_id, &added.peer.endpoint_host);
    vault.save_last_server(server_id);
    Ok(render_config(&pair.private_b64, &added.peer))
}

/// `config_for`, allowing for the network taking a moment to settle: pressing Connect just after Disconnect can land while Windows is still taking the tunnel's routes away.
/// Only a request that never reached Price Chat is tried again. Any answer from it stands.
pub fn config_with_retry(backend: &dyn Backend, vault: &dyn Vault, computer: &str, server_id: i64, pause: &dyn Fn()) -> Result<Zeroizing<String>, String> {
    const TRIES: u32 = 3;
    for _ in 1..TRIES {
        match config_for(backend, vault, computer, server_id) {
            Err(e) if e == crate::api::UNREACHABLE => pause(),
            done => return done,
        }
    }
    config_for(backend, vault, computer, server_id)
}

const REFRESH_EVERY: u64 = 7 * 24 * 3600;

/// Swaps the session for a fresh one about weekly, so regular use never hits the 90-day limit, and a copied token soon stops working. Failing to refresh changes nothing: the
/// current session still works, and a session the server has ended shows up as signed out on the next account call. A first-time session starts the clock without a swap.
pub fn refresh_if_due(backend: &dyn Backend, vault: &dyn Vault, now: u64) {
    let Some(token) = vault.session() else { return };
    let Some(last) = vault.refreshed_at() else {
        vault.save_refreshed_at(now);
        return;
    };
    if now.saturating_sub(last) < REFRESH_EVERY {
        return;
    }
    if let Ok(fresh) = backend.refresh(&token) {
        if vault.save_session(&fresh).is_ok() {
            vault.save_refreshed_at(now);
        }
    }
}

/// The account, with its free trial started if one's on offer, so a new customer can go straight from signing in to connecting. The backend decides whether it's on offer, and allows one per account.
/// If starting it fails, the person still sees the offer and can press the button. Only a signed-out session is an error here.
pub fn account_starting_trial(backend: &dyn Backend, token: &str) -> Result<Account, ApiError> {
    let account = backend.account(token)?;
    if account.eligible || !account.trial.as_ref().is_some_and(|t| t.available) {
        return Ok(account);
    }
    match backend.start_trial(token) {
        Ok(started) => Ok(started),
        Err(e) if e.signed_out() => Err(e),
        Err(_) => Ok(account),
    }
}

/// Online servers this PC already holds a working device for: a key saved here AND the device still listed on the account (one
/// removed on the website no longer works). Connecting to one of these creates no device, so the device limit can't refuse it.
pub fn known_servers(account: &Account, vault: &dyn Vault) -> Vec<i64> {
    account.servers.iter().filter(|s| s.online).filter(|s| vault.device_id(s.id).is_some_and(|id| account.devices.iter().any(|d| d.id == id && d.server_id == s.id))).map(|s| s.id).collect()
}

/// The server to offer first: the last one used if it's still online, else the backend's recommendation, else any online one.
pub fn suggested(servers: &[Server], last: Option<i64>) -> Option<i64> {
    let online = |id: i64| servers.iter().any(|s| s.id == id && s.online);
    last.filter(|&id| online(id)).or_else(|| servers.iter().find(|s| s.online && s.recommended).map(|s| s.id)).or_else(|| servers.iter().find(|s| s.online).map(|s| s.id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::{Device, NewDevice};
    use std::cell::RefCell;
    use std::collections::HashMap;

    #[derive(Default)]
    struct FakeVault {
        session: RefCell<Option<String>>,
        devices: RefCell<HashMap<i64, (i64, String)>>,
        last: RefCell<Option<i64>>,
        refreshed: RefCell<Option<u64>>,
        endpoints: RefCell<HashMap<i64, String>>,
    }

    impl Vault for FakeVault {
        fn session(&self) -> Option<Zeroizing<String>> {
            self.session.borrow().clone().map(Zeroizing::new)
        }
        fn save_session(&self, token: &str) -> Result<(), String> {
            *self.session.borrow_mut() = Some(token.into());
            Ok(())
        }
        fn device(&self, id: i64) -> Option<Saved> {
            self.devices.borrow().get(&id).map(|(d, k)| Saved { device_id: *d, private_b64: Zeroizing::new(k.clone()) })
        }
        fn save_device(&self, s: i64, d: i64, k: &str) -> Result<(), String> {
            self.devices.borrow_mut().insert(s, (d, k.into()));
            Ok(())
        }
        fn forget_device(&self, s: i64) {
            self.devices.borrow_mut().remove(&s);
        }
        fn last_server(&self) -> Option<i64> {
            *self.last.borrow()
        }
        fn save_last_server(&self, s: i64) {
            *self.last.borrow_mut() = Some(s);
        }
        fn refreshed_at(&self) -> Option<u64> {
            *self.refreshed.borrow()
        }
        fn save_refreshed_at(&self, s: u64) {
            *self.refreshed.borrow_mut() = Some(s);
        }
        fn forget_all(&self) {
            *self.session.borrow_mut() = None;
            self.devices.borrow_mut().clear();
        }
        fn endpoint(&self, s: i64) -> Option<String> {
            self.endpoints.borrow().get(&s).cloned()
        }
        fn save_endpoint(&self, s: i64, host: &str) {
            self.endpoints.borrow_mut().insert(s, host.into());
        }
    }

    struct FakeBackend {
        account: RefCell<Result<Account, ApiError>>,
        trial: RefCell<Option<Result<Account, ApiError>>>,
        add: RefCell<Option<ApiError>>,
        calls: RefCell<Vec<String>>,
    }

    fn peer() -> Peer {
        Peer { server_public_key: "SERVERKEY".into(), endpoint_host: "2.31.133.78".into(), listen_port: 51821, addresses: vec!["10.66.0.9/32".into()], dns: vec!["1.1.1.1".into()], allowed_ips: vec!["0.0.0.0/0".into(), "::/0".into()], keepalive: 25 }
    }

    impl Backend for FakeBackend {
        fn account(&self, _: &str) -> Result<Account, ApiError> {
            self.calls.borrow_mut().push("account".into());
            self.account.borrow().clone()
        }
        fn start_trial(&self, _: &str) -> Result<Account, ApiError> {
            self.calls.borrow_mut().push("start_trial".into());
            self.trial.borrow().clone().unwrap_or_else(|| self.account.borrow().clone())
        }
        fn peer(&self, _: &str, id: i64) -> Result<Peer, ApiError> {
            self.calls.borrow_mut().push(format!("peer {id}"));
            Ok(peer())
        }
        fn add_device(&self, _: &str, name: &str, server_id: i64, public_key: &str) -> Result<NewDevice, ApiError> {
            self.calls.borrow_mut().push(format!("add {name} {server_id} {public_key}"));
            if let Some(e) = self.add.borrow().clone() {
                return Err(e);
            }
            Ok(NewDevice { device: Device { id: 50, name: name.into(), server_id }, peer: peer() })
        }
        fn delete_device(&self, _: &str, id: i64) -> Result<(), ApiError> {
            self.calls.borrow_mut().push(format!("delete {id}"));
            Ok(())
        }
        fn token(&self, _: &str, _: &str) -> Result<String, ApiError> {
            Ok("t".into())
        }
        fn logout(&self, _: &str) -> Result<(), ApiError> {
            Ok(())
        }
        fn refresh(&self, _: &str) -> Result<String, ApiError> {
            self.calls.borrow_mut().push("refresh".into());
            if let Some(e) = self.add.borrow().clone() {
                return Err(e);
            }
            Ok("fresh".into())
        }
        fn my_ip(&self) -> Result<String, ApiError> {
            Ok("1.2.3.4".into())
        }
        fn support(&self, _: &str, kind: &str, _: &str, _: &str, _: Option<&str>) -> Result<(), ApiError> {
            self.calls.borrow_mut().push(format!("support {kind}"));
            Ok(())
        }
    }

    fn server(id: i64, online: bool, recommended: bool) -> Server {
        Server { id, name: format!("s{id}"), region: String::new(), country_code: "DE".into(), online, recommended, load: None, live_load: None, adblock: false, proxy_host: None }
    }

    fn account(devices: Vec<Device>) -> Account {
        Account { enabled: true, eligible: true, reason: None, max_devices: 3, servers: vec![server(2, true, false), server(3, false, false)], devices, plan: None, trial: None }
    }

    fn backend(a: Result<Account, ApiError>) -> FakeBackend {
        FakeBackend { account: RefCell::new(a), trial: RefCell::new(None), add: RefCell::new(None), calls: RefCell::new(vec![]) }
    }

    fn offering_trial() -> Account {
        Account { eligible: false, reason: Some("Start your free trial".into()), trial: Some(crate::api::Trial { available: true, days: 30 }), ..account(vec![]) }
    }

    #[test]
    fn a_new_customer_has_their_trial_started_and_can_connect() {
        let b = backend(Ok(offering_trial()));
        *b.trial.borrow_mut() = Some(Ok(account(vec![])));
        let got = account_starting_trial(&b, "token").unwrap();
        assert!(got.eligible);
        assert_eq!(*b.calls.borrow(), vec!["account", "start_trial"]);
    }

    #[test]
    fn no_trial_is_started_unless_the_backend_offers_one() {
        let closed = Account { enabled: false, eligible: false, reason: Some("unavailable".into()), trial: Some(crate::api::Trial { available: false, days: 30 }), ..account(vec![]) };
        let used = Account { eligible: false, reason: Some("expired".into()), trial: None, ..account(vec![]) };
        for a in [closed, used, account(vec![])] {
            let b = backend(Ok(a.clone()));
            assert_eq!(account_starting_trial(&b, "token").unwrap(), a);
            assert_eq!(*b.calls.borrow(), vec!["account"]);
        }
    }

    #[test]
    fn a_failed_trial_start_leaves_the_offer_showing_and_a_signed_out_one_says_so() {
        let refused = ApiError { status: 409, message: "no".into() };
        let b = backend(Ok(offering_trial()));
        *b.trial.borrow_mut() = Some(Err(refused));
        assert_eq!(account_starting_trial(&b, "token").unwrap(), offering_trial());
        let gone = ApiError { status: 401, message: "no".into() };
        let b = backend(Ok(offering_trial()));
        *b.trial.borrow_mut() = Some(Err(gone.clone()));
        assert_eq!(account_starting_trial(&b, "token").unwrap_err(), gone);
    }

    fn signed_in() -> FakeVault {
        let v = FakeVault::default();
        v.save_session("token").unwrap();
        v
    }

    #[test]
    fn the_first_connect_makes_a_key_registers_only_the_public_half_and_keeps_the_private_one() {
        let (b, v) = (backend(Ok(account(vec![]))), signed_in());
        let config = config_for(&b, &v, "DESKTOP-1", 2).unwrap();
        let calls = b.calls.borrow();
        let add = calls.iter().find(|c| c.starts_with("add ")).unwrap();
        let (private, public) = { let d = v.devices.borrow(); let k = d.get(&2).unwrap().1.clone(); (k, add.rsplit(' ').next().unwrap().to_string()) };
        assert_ne!(private, public);
        assert!(!add.contains(&private), "the private key never goes to the server");
        assert!(config.contains(&format!("PrivateKey = {private}")));
        assert!(config.contains("PublicKey = SERVERKEY") && config.contains("Endpoint = 2.31.133.78:51821") && config.contains("PersistentKeepalive = 25"));
        assert_eq!(v.devices.borrow().get(&2).unwrap().0, 50);
        assert_eq!(v.last_server(), Some(2));
    }

    fn private_key_in(config: &str) -> String {
        config.lines().find_map(|l| l.strip_prefix("PrivateKey = ")).unwrap().to_string()
    }

    /// Connect, Disconnect, Connect, through the real encrypted store: the second connect is the first time a saved key is read back, which is the path a fake store can't prove.
    #[cfg(windows)]
    #[test]
    fn reconnecting_reads_the_same_key_back_through_the_real_store() {
        let mut raw = [0u8; 8];
        getrandom::getrandom(&mut raw).unwrap();
        let dir = std::env::temp_dir().join(format!("kiwi-flow-{}", u64::from_le_bytes(raw)));
        let v = crate::store::FileVault::at(dir.clone()).unwrap();
        v.save_session("token").unwrap();
        let b = backend(Ok(account(vec![])));
        let first = config_for(&b, &v, "DESKTOP-1", 2).unwrap();
        // the backend now lists the device the first connect registered, and Disconnect changes nothing on this side
        *b.account.borrow_mut() = Ok(account(vec![Device { id: 50, name: "DESKTOP-1 (2)".into(), server_id: 2 }]));
        let again = config_for(&b, &v, "DESKTOP-1", 2).unwrap();
        assert_eq!(private_key_in(&first), private_key_in(&again));
        kiwivpn::config::parse(&format!("{}", again.replace("SERVERKEY", &keys::generate().unwrap().public_b64))).expect("the service must accept a reconnect's config too");
        let calls = b.calls.borrow();
        assert_eq!(calls.iter().filter(|c| c.starts_with("add ")).count(), 1, "no second device");
        assert!(calls.iter().any(|c| c == "peer 50"));
        assert!(!calls.iter().any(|c| c.starts_with("delete ")), "and the working device isn't removed: {calls:?}");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_network_gap_just_after_disconnect_is_waited_out() {
        let gap = || Err(ApiError { status: 0, message: crate::api::UNREACHABLE.into() });
        let (b, v) = (backend(gap()), signed_in());
        let pauses = std::cell::Cell::new(0);
        let config = config_with_retry(&b, &v, "PC", 2, &|| {
            pauses.set(pauses.get() + 1);
            if pauses.get() == 2 {
                *b.account.borrow_mut() = Ok(account(vec![]));
            }
        });
        assert!(config.is_ok());
        assert_eq!(pauses.get(), 2);
        assert_eq!(v.session().unwrap().as_str(), "token", "a network problem never signs anyone out");
    }

    #[test]
    fn it_stops_retrying_when_the_network_stays_down_and_never_retries_an_answer() {
        let gap = || Err(ApiError { status: 0, message: crate::api::UNREACHABLE.into() });
        let (b, v) = (backend(gap()), signed_in());
        let pauses = std::cell::Cell::new(0);
        assert_eq!(config_with_retry(&b, &v, "PC", 2, &|| pauses.set(pauses.get() + 1)).unwrap_err(), crate::api::UNREACHABLE);
        assert_eq!(pauses.get(), 2);
        let (b, v) = (backend(Err(ApiError { status: 403, message: "no".into() })), signed_in());
        let pauses = std::cell::Cell::new(0);
        assert_eq!(config_with_retry(&b, &v, "PC", 2, &|| pauses.set(pauses.get() + 1)).unwrap_err(), "no");
        assert_eq!(pauses.get(), 0);
    }

    #[test]
    fn the_config_is_one_the_service_accepts() {
        let pair = keys::generate().unwrap();
        let mut p = peer();
        p.server_public_key = pair.public_b64.clone();
        let text = render_config(&pair.private_b64, &p);
        let parsed = kiwivpn::config::parse(&text).expect("the service's own parser must accept what the app builds");
        assert_eq!(parsed.private_key, *pair.private_b64);
        assert_eq!(parsed.peer_public_key, pair.public_b64);
        assert_eq!(parsed.endpoint, "2.31.133.78:51821");
        assert_eq!(parsed.keepalive, 25);
    }

    #[test]
    fn a_later_connect_reuses_the_device_and_makes_no_new_one() {
        let (b, v) = (backend(Ok(account(vec![Device { id: 50, name: "x".into(), server_id: 2 }]))), signed_in());
        v.save_device(2, 50, "SAVEDKEY").unwrap();
        let config = config_for(&b, &v, "PC", 2).unwrap();
        assert!(config.contains("PrivateKey = SAVEDKEY"));
        assert!(b.calls.borrow().contains(&"peer 50".to_string()));
        assert!(!b.calls.borrow().iter().any(|c| c.starts_with("add ")));
    }

    #[test]
    fn a_device_removed_on_the_website_is_replaced() {
        let (b, v) = (backend(Ok(account(vec![]))), signed_in());
        v.save_device(2, 44, "OLDKEY").unwrap();
        let config = config_for(&b, &v, "PC", 2).unwrap();
        assert!(!config.contains("OLDKEY"));
        assert_eq!(v.devices.borrow().get(&2).unwrap().0, 50);
    }

    #[test]
    fn a_reinstall_clears_its_own_unusable_device_and_nobody_elses() {
        let mine = device_name("PC", &server(2, true, false));
        let (b, v) = (backend(Ok(account(vec![Device { id: 9, name: mine, server_id: 2 }, Device { id: 10, name: "My iPhone".into(), server_id: 2 }]))), signed_in());
        config_for(&b, &v, "PC", 2).unwrap();
        let calls = b.calls.borrow();
        assert!(calls.contains(&"delete 9".to_string()));
        assert!(!calls.contains(&"delete 10".to_string()));
    }

    #[test]
    fn says_plainly_when_it_can_not_connect() {
        let v = signed_in();
        assert!(config_for(&backend(Ok(account(vec![]))), &v, "PC", 3).unwrap_err().contains("offline"));
        assert!(config_for(&backend(Ok(account(vec![]))), &v, "PC", 99).unwrap_err().contains("no longer available"));
        let mut locked = account(vec![]);
        locked.eligible = false;
        locked.reason = Some("Your time has run out.".into());
        assert_eq!(config_for(&backend(Ok(locked)), &v, "PC", 2).unwrap_err(), "Your time has run out.");
        let full = backend(Ok(account(vec![])));
        *full.add.borrow_mut() = Some(ApiError { status: 409, message: "x".into() });
        assert!(config_for(&full, &v, "PC", 2).unwrap_err().contains("My Devices"));
        assert!(v.device(2).is_none(), "no key is kept for a device that was refused");
    }

    #[test]
    fn an_expired_session_clears_what_it_held() {
        let v = signed_in();
        v.save_device(2, 50, "KEY").unwrap();
        let e = config_for(&backend(Err(ApiError { status: 401, message: "x".into() })), &v, "PC", 2).unwrap_err();
        assert_eq!(e, SIGNED_OUT);
        assert!(v.session().is_none() && v.device(2).is_none());
        assert_eq!(config_for(&backend(Ok(account(vec![]))), &FakeVault::default(), "PC", 2).unwrap_err(), SIGNED_OUT);
    }

    #[test]
    fn refreshes_weekly_and_keeps_working_if_it_cant() {
        const DAY: u64 = 24 * 3600;
        let (b, v) = (backend(Ok(account(vec![]))), signed_in());
        refresh_if_due(&b, &v, 1000);
        assert!(b.calls.borrow().is_empty(), "a new session just starts the clock");
        refresh_if_due(&b, &v, 1000 + 6 * DAY);
        assert!(b.calls.borrow().is_empty());
        refresh_if_due(&b, &v, 1000 + 8 * DAY);
        assert_eq!(v.session().unwrap().as_str(), "fresh");
        assert_eq!(v.refreshed_at(), Some(1000 + 8 * DAY));
        let (down, kept) = (backend(Ok(account(vec![]))), signed_in());
        kept.save_refreshed_at(0);
        *down.add.borrow_mut() = Some(ApiError { status: 0, message: "offline".into() });
        refresh_if_due(&down, &kept, 30 * DAY);
        assert_eq!(kept.session().unwrap().as_str(), "token", "no connection: the session stays");
        assert_eq!(kept.refreshed_at(), Some(0));
        refresh_if_due(&backend(Ok(account(vec![]))), &FakeVault::default(), 30 * DAY);
    }

    #[test]
    fn device_names_fit_the_backend_and_stay_clean() {
        let s = server(12, true, false);
        assert_eq!(device_name("DESKTOP-F6OQ1KH", &s), "DESKTOP-F6OQ1KH (12)");
        assert!(device_name(&"X".repeat(200), &s).len() <= 32);
        assert_eq!(device_name("  !!", &s), "Windows PC (12)");
    }

    #[test]
    fn known_servers_are_the_ones_with_a_saved_key_and_a_device_still_on_the_account() {
        let mut a = account(vec![Device { id: 50, name: "PC (2)".into(), server_id: 2 }, Device { id: 60, name: "Pixel".into(), server_id: 4 }]);
        a.servers.push(server(4, true, false));
        a.servers.push(server(5, true, false));
        let v = signed_in();
        v.save_device(2, 50, "KEY2").unwrap(); // saved here and listed: known
        v.save_device(5, 77, "KEY5").unwrap(); // saved here but removed on the website: not known
        v.save_device(3, 51, "KEY3").unwrap(); // offline server
        assert_eq!(known_servers(&a, &v), vec![2], "server 4's device is someone else's (the phone): no key here");
        assert!(known_servers(&a, &FakeVault::default()).is_empty());
    }

    #[test]
    fn connecting_to_a_known_server_creates_no_device_and_remembers_the_endpoint() {
        let a = account(vec![Device { id: 50, name: "PC (2)".into(), server_id: 2 }]);
        let (b, v) = (backend(Ok(a.clone())), signed_in());
        v.save_device(2, 50, "SAVEDKEY").unwrap();
        assert_eq!(known_servers(&a, &v), vec![2]);
        config_with_retry(&b, &v, "PC", 2, &|| {}).unwrap();
        assert!(!b.calls.borrow().iter().any(|c| c.starts_with("add ") || c.starts_with("delete ")), "{:?}", b.calls.borrow());
        assert_eq!(v.endpoint(2).as_deref(), Some("2.31.133.78"));
        // and a new device's endpoint is remembered too
        let (b, v) = (backend(Ok(account(vec![]))), signed_in());
        config_for(&b, &v, "PC", 2).unwrap();
        assert_eq!(v.endpoint(2).as_deref(), Some("2.31.133.78"));
    }

    #[test]
    fn suggests_the_last_server_then_the_recommended_then_any_online() {
        let servers = vec![server(1, false, false), server(2, true, false), server(3, true, true)];
        assert_eq!(suggested(&servers, Some(2)), Some(2));
        assert_eq!(suggested(&servers, Some(1)), Some(3));
        assert_eq!(suggested(&servers, None), Some(3));
        assert_eq!(suggested(&[server(2, true, false)], None), Some(2));
        assert_eq!(suggested(&[server(1, false, false)], None), None);
    }
}
