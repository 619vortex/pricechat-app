//! Everything that touches Windows: the tunnel service, the driver's statistics, and the named pipe the app talks through.

use crate::config;
use crate::firewall::Guard;
use crate::guard::{self, Plan, Protect};
use crate::protocol::{self, Caller, Request, ServiceState, Status, PIPE_NAME};
use crate::update_win::{self, Updater};
use std::ffi::c_void;
use std::io::Write;
use std::mem::{size_of, zeroed};
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use windows_sys::Win32::Foundation::*;
use windows_sys::Win32::Security::Authorization::{ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo, SDDL_REVISION_1, SE_KERNEL_OBJECT};
use windows_sys::Win32::Security::{GetTokenInformation, TokenElevation, OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES, TOKEN_ELEVATION, TOKEN_QUERY};
use windows_sys::Win32::Storage::FileSystem::*;
use windows_sys::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryW};
use windows_sys::Win32::System::Pipes::*;
use windows_sys::Win32::System::Services::*;
use windows_sys::Win32::System::Threading::{OpenProcess, OpenProcessToken, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION};

pub const OWN_SERVICE: &str = "KiwiVPN";
pub const TUNNEL_NAME: &str = "kiwi";
/// The one program, in this service's own folder, that may change the tunnel without administrator rights.
pub const APP_EXE: &str = "KiwiVPN.exe";
/// Clients being served at once. A program opening endless connections gets refused rather than running the service out of threads.
const MAX_CLIENTS: usize = 64;
const TUNNEL_SERVICE: &str = "WireGuardTunnel$kiwi";
/// SYSTEM and the Administrators group only.
pub(crate) const PRIVATE_SDDL: &str = "D:P(A;OICI;GA;;;SY)(A;OICI;GA;;;BA)";
/// What the signed-in person may do on the pipe: read data, write data, read its attributes and owner, wait on it. Exactly this, because for a pipe the "append data" part of
/// generic write is also the right to create another instance of it, which would let any program of theirs pose as the service on the service's own pipe, under its owner.
pub const PIPE_CLIENT_ACCESS: u32 = 0x0012_0083;
/// SYSTEM fully, the signed-in person PIPE_CLIENT_ACCESS. Nobody else, nothing over the network (see PIPE_REJECT_REMOTE_CLIENTS).
const PIPE_SDDL: &str = "D:P(A;;GA;;;SY)(A;;0x120083;;;IU)";
const SERVICE_SID_TYPE_UNRESTRICTED: u32 = 1;

pub(crate) fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

pub fn last_error() -> String {
    format!("Windows error {}", unsafe { GetLastError() })
}

fn exe_dir() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    exe.parent().map(PathBuf::from).ok_or_else(|| "no install folder".to_string())
}

/// After Windows restarts: the config only exists while someone wants the tunnel, because Disconnect deletes it. So if it's there, bring the tunnel back. The network may not be up yet
/// when the service starts, hence the tries. Pressing Disconnect at any point ends them, because the config is gone.
fn resume_after_restart(tunnel: Arc<Tunnel>) {
    tunnel.resuming.store(true, Ordering::SeqCst);
    if conf_path().map(|p| p.exists()).unwrap_or(false) {
        *tunnel.resumed_at.lock().unwrap() = Some(now_seconds());
        // the kill switch comes back before the tunnel does, so nothing leaks while the network comes up
        if let Some((plan, protect)) = saved_plan() {
            if let Err(e) = tunnel.engage(&plan, protect.strict) {
                log_event(&format!("guard: couldn't restore after the restart: {e}"));
            }
        }
    }
    for _ in 0..30 {
        if tunnel.try_resume() {
            break;
        }
        std::thread::sleep(Duration::from_secs(10));
    }
    tunnel.resuming.store(false, Ordering::SeqCst);
}

/// Under the install folder, which only administrators can write to.
fn tunnels_dir() -> Result<PathBuf, String> {
    Ok(exe_dir()?.join("tunnels"))
}

fn conf_path() -> Result<PathBuf, String> {
    Ok(tunnels_dir()?.join(format!("{TUNNEL_NAME}.conf")))
}

/// What the kill switch was asked to do for the wanted tunnel, so it comes back with it after a restart. Deleted with the config.
fn guard_path() -> Result<PathBuf, String> {
    Ok(tunnels_dir()?.join(format!("{TUNNEL_NAME}.guard")))
}

/// Kiwi's two programs, which the kill switch lets through: this one (it also runs the tunnel) and the app beside it.
fn app_paths() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        out.push(exe);
    }
    if let Ok(dir) = exe_dir() {
        out.push(dir.join(APP_EXE));
    }
    out
}

fn plan_of(protect: &Protect, allow: Vec<guard::Net>, addresses: &[String], endpoint: Option<std::net::SocketAddr>) -> Plan {
    Plan { kill_switch: protect.kill_switch, allow_lan: protect.allow_lan, tunnel: guard::tunnel_nets(addresses), allow, apps: 2, endpoint, block_dot: protect.block_dot }
}

fn save_plan(protect: &Protect, addresses: &[String], endpoint: Option<std::net::SocketAddr>) {
    let Ok(path) = guard_path() else { return };
    let text = serde_json::json!({ "protect": protect, "addresses": addresses, "endpoint": endpoint.map(|e| e.to_string()) }).to_string();
    if std::fs::write(path, text).is_err() {
        log_event("guard: couldn't save the kill switch's settings for a restart");
    }
}

fn saved_plan() -> Option<(Plan, Protect)> {
    let text = std::fs::read_to_string(guard_path().ok()?).ok()?;
    let v: serde_json::Value = serde_json::from_str(&text).ok()?;
    let protect: Protect = serde_json::from_value(v.get("protect")?.clone()).ok()?;
    let addresses: Vec<String> = serde_json::from_value(v.get("addresses")?.clone()).ok()?;
    // absent in a 2.1 file: the service's UDP may then go anywhere until the next connect
    let endpoint = v.get("endpoint").and_then(|e| e.as_str()).and_then(|e| e.parse().ok());
    let allow = protect.check().ok()?;
    Some((plan_of(&protect, allow, &addresses, endpoint), protect))
}

/// A host name endpoint is looked up now, before the kill switch is on: once it is, DNS only works inside the tunnel.
fn resolved(endpoint: &str) -> String {
    use std::net::{SocketAddr, ToSocketAddrs};
    if endpoint.parse::<SocketAddr>().is_ok() {
        return endpoint.to_string();
    }
    match endpoint.to_socket_addrs() {
        Ok(found) => {
            let found: Vec<SocketAddr> = found.collect();
            found.iter().find(|a| a.is_ipv4()).or(found.first()).map(ToString::to_string).unwrap_or_else(|| endpoint.to_string())
        }
        Err(_) => endpoint.to_string(),
    }
}

pub(crate) struct Descriptor(PSECURITY_DESCRIPTOR);

impl Descriptor {
    pub(crate) fn new(sddl: &str) -> Result<Self, String> {
        let mut sd: PSECURITY_DESCRIPTOR = null_mut();
        let ok = unsafe { ConvertStringSecurityDescriptorToSecurityDescriptorW(wide(sddl).as_ptr(), SDDL_REVISION_1, &mut sd, null_mut()) };
        if ok == 0 {
            return Err(last_error());
        }
        Ok(Descriptor(sd))
    }
    pub(crate) fn attributes(&self) -> SECURITY_ATTRIBUTES {
        SECURITY_ATTRIBUTES { nLength: size_of::<SECURITY_ATTRIBUTES>() as u32, lpSecurityDescriptor: self.0, bInheritHandle: 0 }
    }
}

impl Drop for Descriptor {
    fn drop(&mut self) {
        unsafe { LocalFree(self.0 as _) };
    }
}

// --- the tunnel ---------------------------------------------------------------------------------------------------

struct Sc(SC_HANDLE);

impl Drop for Sc {
    fn drop(&mut self) {
        unsafe { CloseServiceHandle(self.0) };
    }
}

fn manager() -> Result<Sc, String> {
    let h = unsafe { OpenSCManagerW(null(), null(), SC_MANAGER_ALL_ACCESS) };
    if h.is_null() {
        return Err(format!("Can't open the service manager: {}", last_error()));
    }
    Ok(Sc(h))
}

fn open_service(scm: &Sc, name: &str) -> Option<Sc> {
    let h = unsafe { OpenServiceW(scm.0, wide(name).as_ptr(), SERVICE_ALL_ACCESS) };
    (!h.is_null()).then_some(Sc(h))
}

fn service_state(service: &Sc) -> ServiceState {
    let mut s: SERVICE_STATUS = unsafe { zeroed() };
    if unsafe { QueryServiceStatus(service.0, &mut s) } == 0 {
        return ServiceState::Missing;
    }
    match s.dwCurrentState {
        SERVICE_RUNNING => ServiceState::Running,
        SERVICE_START_PENDING | SERVICE_CONTINUE_PENDING => ServiceState::Starting,
        SERVICE_STOP_PENDING | SERVICE_PAUSE_PENDING => ServiceState::Stopping,
        _ => ServiceState::Stopped,
    }
}

fn stop_and_delete(name: &str) -> Result<(), String> {
    let scm = manager()?;
    let Some(service) = open_service(&scm, name) else { return Ok(()) };
    stop_and_wait(&service, Duration::from_secs(20));
    unsafe { DeleteService(service.0) };
    Ok(())
}

/// Asks the tunnel to stop, and waits. True once it has stopped, or there's nothing left to stop. The stop is asked for again on every look:
/// Windows refuses it while a service is still starting, and a tunnel asked only then would finish starting and keep the PC's routes.
fn stop_and_wait(service: &Sc, limit: Duration) -> bool {
    let start = Instant::now();
    loop {
        match service_state(service) {
            ServiceState::Stopped | ServiceState::Missing => return true,
            ServiceState::Running | ServiceState::Starting => {
                let mut s: SERVICE_STATUS = unsafe { zeroed() };
                unsafe { ControlService(service.0, SERVICE_CONTROL_STOP, &mut s) };
            }
            ServiceState::Stopping => {}
        }
        if start.elapsed() >= limit {
            return false;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

fn exit_code(service: &Sc) -> u32 {
    let mut s: SERVICE_STATUS = unsafe { zeroed() };
    if unsafe { QueryServiceStatus(service.0, &mut s) } == 0 {
        return 0;
    }
    s.dwWin32ExitCode
}

/// The Kiwi tunnel's registration, made if it isn't there. It stays between connections, holding no key, because deleting it is what can stop a later Connect: Windows only finishes
/// deleting a service once every handle to it has closed, and anything can hold one, such as the Services window or a PowerShell that ran Get-Service.
fn tunnel_service(scm: &Sc, conf: &str) -> Result<Sc, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let command = wide(&format!("\"{}\" tunnel \"{}\"", exe.display(), conf));
    let service = match open_service(scm, TUNNEL_SERVICE) {
        Some(existing) => {
            let ok = unsafe { ChangeServiceConfigW(existing.0, SERVICE_NO_CHANGE, SERVICE_NO_CHANGE, SERVICE_NO_CHANGE, command.as_ptr(), null(), null_mut(), null(), null(), null(), null()) };
            if ok != 0 {
                existing
            } else if unsafe { GetLastError() } == ERROR_SERVICE_MARKED_FOR_DELETE {
                drop(existing); // left being deleted by an uninstall, or by an older version
                create_tunnel_service(scm, &command)?
            } else {
                return Err(format!("Can't configure the tunnel: {}", last_error()));
            }
        }
        None => create_tunnel_service(scm, &command)?,
    };
    // WireGuard's documentation calls this essential: the driver's firewall rules depend on the service's SID.
    let mut sid = SERVICE_SID_INFO { dwServiceSidType: SERVICE_SID_TYPE_UNRESTRICTED };
    if unsafe { ChangeServiceConfig2W(service.0, SERVICE_CONFIG_SERVICE_SID_INFO, &mut sid as *mut _ as *mut c_void) } == 0 {
        return Err(format!("Can't configure the tunnel: {}", last_error()));
    }
    Ok(service)
}

fn create_tunnel_service(scm: &Sc, command: &[u16]) -> Result<Sc, String> {
    let deps: Vec<u16> = "Nsi\0TcpIp\0\0".encode_utf16().collect();
    let start = Instant::now();
    let h = loop {
        let h = unsafe {
            CreateServiceW(scm.0, wide(TUNNEL_SERVICE).as_ptr(), wide("Kiwi VPN tunnel").as_ptr(), SERVICE_ALL_ACCESS, SERVICE_WIN32_OWN_PROCESS, SERVICE_DEMAND_START, SERVICE_ERROR_NORMAL,
                           command.as_ptr(), null(), null_mut(), deps.as_ptr(), null(), null())
        };
        if !h.is_null() || unsafe { GetLastError() } != ERROR_SERVICE_MARKED_FOR_DELETE || start.elapsed() > Duration::from_secs(30) {
            break h;
        }
        std::thread::sleep(Duration::from_millis(200));
    };
    if h.is_null() {
        return Err(if unsafe { GetLastError() } == ERROR_SERVICE_MARKED_FOR_DELETE {
            "Windows is still removing the previous connection. Close the Services window if it's open, or restart your PC.".to_string()
        } else {
            format!("Can't create the tunnel: {}", last_error())
        });
    }
    Ok(Sc(h))
}

/// Makes the tunnel's adapter the one Windows prefers (interface metric 0, IPv4 and IPv6), so its DNS server is asked first. WireGuard
/// only does this itself when AllowedIPs holds a default route, which Kiwi sends as two halves (see `guard::without_default_routes`):
/// without it, Windows kept asking the regular network's DNS first, which the DNS guard blocks, and names took 13 s to resolve after
/// every connect. True once the adapter exists and is set.
fn prefer_tunnel() -> bool {
    use windows_sys::Win32::NetworkManagement::IpHelper::{GetIpInterfaceEntry, InitializeIpInterfaceEntry, SetIpInterfaceEntry, MIB_IPINTERFACE_ROW};
    use windows_sys::Win32::Networking::WinSock::{AF_INET, AF_INET6};
    type Open = unsafe extern "system" fn(*const u16) -> *mut c_void;
    type Close = unsafe extern "system" fn(*mut c_void);
    type GetLuid = unsafe extern "system" fn(*mut c_void, *mut u64);
    let Ok(dir) = exe_dir() else { return false };
    let dll = wide(&dir.join("wireguard.dll").display().to_string());
    unsafe {
        let lib = LoadLibraryW(dll.as_ptr());
        if lib.is_null() {
            return false;
        }
        let (Some(open), Some(close), Some(luid_of)) = (
            GetProcAddress(lib, c"WireGuardOpenAdapter".as_ptr() as *const u8),
            GetProcAddress(lib, c"WireGuardCloseAdapter".as_ptr() as *const u8),
            GetProcAddress(lib, c"WireGuardGetAdapterLUID".as_ptr() as *const u8),
        ) else {
            return false;
        };
        let open: Open = std::mem::transmute(open);
        let close: Close = std::mem::transmute(close);
        let luid_of: GetLuid = std::mem::transmute(luid_of);
        let adapter = open(wide(TUNNEL_NAME).as_ptr());
        if adapter.is_null() {
            return false;
        }
        let mut luid = 0u64;
        luid_of(adapter, &mut luid);
        close(adapter);
        let mut set_any = false;
        for family in [AF_INET, AF_INET6] {
            let mut row: MIB_IPINTERFACE_ROW = zeroed();
            InitializeIpInterfaceEntry(&mut row);
            row.Family = family;
            row.InterfaceLuid.Value = luid;
            if GetIpInterfaceEntry(&mut row) != 0 {
                continue; // that family isn't on the adapter (yet)
            }
            set_any = true;
            if row.UseAutomaticMetric == 0 && row.Metric == 0 {
                continue;
            }
            row.UseAutomaticMetric = 0;
            row.Metric = 0;
            if family == AF_INET {
                row.SitePrefixLength = 0; // Windows requires it unset when changing an IPv4 interface
            }
            let done = SetIpInterfaceEntry(&mut row);
            log_event(&format!("tunnel: interface metric set to 0 for {} ({})", if family == AF_INET { "IPv4" } else { "IPv6" }, if done == 0 { "done".to_string() } else { format!("Windows error {done}") }));
        }
        set_any
    }
}

/// True while the tunnel's network adapter exists. It goes as the old tunnel exits, and a new one can't take its name until then.
fn adapter_exists() -> bool {
    type Open = unsafe extern "system" fn(*const u16) -> *mut c_void;
    type Close = unsafe extern "system" fn(*mut c_void);
    let Ok(dir) = exe_dir() else { return false };
    let dll = wide(&dir.join("wireguard.dll").display().to_string());
    unsafe {
        let lib = LoadLibraryW(dll.as_ptr());
        if lib.is_null() {
            return false;
        }
        let (Some(open), Some(close)) = (GetProcAddress(lib, c"WireGuardOpenAdapter".as_ptr() as *const u8), GetProcAddress(lib, c"WireGuardCloseAdapter".as_ptr() as *const u8)) else { return false };
        let open: Open = std::mem::transmute(open);
        let close: Close = std::mem::transmute(close);
        let adapter = open(wide(TUNNEL_NAME).as_ptr());
        if adapter.is_null() {
            return false;
        }
        close(adapter);
        true
    }
}

fn wait_for_adapter_to_go(limit: Duration) {
    let start = Instant::now();
    while adapter_exists() && start.elapsed() < limit {
        std::thread::sleep(Duration::from_millis(200));
    }
}

pub struct Tunnel {
    error: Mutex<Option<String>>,
    since: Mutex<Option<u64>>,
    /// One connect or disconnect at a time: two running together could delete each other's tunnel.
    changing: Mutex<()>,
    /// Bringing the tunnel back after a restart, which can take minutes if the network is slow to come up. It isn't a failure while that's going on.
    resuming: AtomicBool,
    /// When the person's latest Connect started the tunnel, for giving up if the server never answers.
    started: Mutex<Option<u64>>,
    /// The rules in force while a tunnel is wanted (see `guard`). None: nothing is blocked.
    guard: Mutex<Option<Guard>>,
    /// Restarts of a tunnel that stopped by itself under the kill switch: how many, and the last one's time.
    restarts: Mutex<(u32, u64)>,
    /// When a tunnel was brought back after Windows restarted, until it reaches its server.
    resumed_at: Mutex<Option<u64>>,
    ever_connected: AtomicBool,
    /// When the app paused the tunnel to switch servers, keeping the kill switch on.
    paused_at: Mutex<Option<u64>>,
    /// 2.2: the strict kill switch's persistent filters are in place (they outlive this process; read from Windows at start).
    strict: AtomicBool,
}

fn now_seconds() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

/// The service's own record of what happened to the tunnel, in an administrators-only folder. Only fixed text, states and Windows error codes: never a config or a key.
pub(crate) fn log_event(message: &str) {
    let Ok(dir) = exe_dir().map(|d| d.join("logs")) else { return };
    if let Ok(sd) = Descriptor::new(PRIVATE_SDDL) {
        let attrs = sd.attributes();
        unsafe { CreateDirectoryW(wide(&dir.display().to_string()).as_ptr(), &attrs) };
    }
    let path = dir.join("service.log");
    if std::fs::metadata(&path).map(|m| m.len() > 1024 * 1024).unwrap_or(false) {
        let _ = std::fs::rename(&path, dir.join("service.old.log"));
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{} {message}", now_seconds());
    }
}

const STOP_LIMIT: Duration = Duration::from_secs(30);
const ADAPTER_LIMIT: Duration = Duration::from_secs(10);

fn remove_conf() {
    if let Ok(path) = conf_path() {
        let _ = std::fs::remove_file(path); // holds the device's private key: never left behind
    }
    if let Ok(path) = guard_path() {
        let _ = std::fs::remove_file(path);
    }
}

impl Tunnel {
    pub fn new() -> Self {
        Tunnel {
            error: Mutex::new(None),
            since: Mutex::new(None),
            changing: Mutex::new(()),
            resuming: AtomicBool::new(false),
            started: Mutex::new(None),
            guard: Mutex::new(None),
            restarts: Mutex::new((0, 0)),
            resumed_at: Mutex::new(None),
            ever_connected: AtomicBool::new(false),
            paused_at: Mutex::new(None),
            strict: AtomicBool::new(false),
        }
    }

    /// Puts a plan's rules in force, replacing any before them only once the new ones hold. `strict`: also persistently (audit M3),
    /// first, so the persistent set already allows the new server before the tunnel starts. A plan without it removes an earlier
    /// strict set once the new rules hold.
    fn engage(&self, plan: &Plan, strict: bool) -> Result<(), String> {
        let apps = app_paths();
        let refs: Vec<&Path> = apps.iter().map(PathBuf::as_path).collect();
        if strict && plan.kill_switch {
            let (count, boot) = crate::firewall::strict_engage(plan, &refs)?;
            self.strict.store(true, Ordering::SeqCst);
            log_event(&format!("guard: strict kill switch on, {count} persistent filters, boot-time {}", match boot {
                Ok(n) => format!("{n} filters"),
                Err(e) => format!("not added ({e})"),
            }));
        }
        let guard = Guard::engage(plan, &refs)?;
        log_event(&format!("guard: on, kill switch {}, local network {}, {} kept off, server {}, DNS over TLS blocked {}, {} filters", plan.kill_switch, plan.allow_lan, plan.allow.len(),
                           if plan.endpoint.is_some() { "known" } else { "unknown" }, plan.block_dot, guard.filters()));
        *self.guard.lock().unwrap() = Some(guard);
        if !(strict && plan.kill_switch) {
            self.remove_strict("a connect without the strict kill switch");
        }
        Ok(())
    }

    /// Removes the strict kill switch's persistent filters, if there are any.
    fn remove_strict(&self, why: &str) {
        if !self.strict.load(Ordering::SeqCst) && !crate::firewall::strict_present() {
            return;
        }
        match crate::firewall::strict_remove() {
            Ok(n) => {
                self.strict.store(false, Ordering::SeqCst);
                log_event(&format!("guard: strict kill switch off ({why}), {n} filters removed"));
            }
            Err(e) => log_event(&format!("guard: couldn't remove the strict kill switch ({why}): {e}")),
        }
    }

    pub fn strict_held(&self) -> bool {
        self.strict.load(Ordering::SeqCst)
    }

    fn release(&self, why: &str) {
        if self.guard.lock().unwrap().take().is_some() {
            log_event(&format!("guard: off ({why})"));
        }
    }

    fn kill_active(&self) -> bool {
        self.guard.lock().unwrap().as_ref().is_some_and(Guard::kill_switch) || self.strict.load(Ordering::SeqCst)
    }

    pub fn connect(&self, text: &str, protect: &Protect) -> Status {
        let _one_at_a_time = self.changing.lock().unwrap();
        *self.error.lock().unwrap() = None;
        *self.since.lock().unwrap() = None;
        *self.started.lock().unwrap() = None;
        *self.restarts.lock().unwrap() = (0, 0);
        *self.resumed_at.lock().unwrap() = None;
        *self.paused_at.lock().unwrap() = None;
        self.ever_connected.store(false, Ordering::SeqCst);
        log_event(&format!("connect: requested, kill switch {}, strict {}", protect.kill_switch, protect.strict));
        let allow = match protect.check() {
            Ok(a) => a,
            Err(e) => return self.finish(Status::failed(e)),
        };
        let mut cfg = match config::parse(text) {
            Ok(c) => c,
            Err(e) => {
                log_event("connect: the config was refused");
                return self.finish(Status::failed(e));
            }
        };
        cfg.allowed_ips = guard::without_default_routes(&cfg.allowed_ips);
        cfg.endpoint = resolved(&cfg.endpoint);
        let endpoint = cfg.endpoint.parse::<std::net::SocketAddr>().ok();
        let plan = plan_of(protect, allow, &cfg.addresses, endpoint);
        // on before the old tunnel closes, so switching servers never leaks
        if let Err(e) = self.engage(&plan, protect.strict) {
            log_event(&format!("guard: couldn't turn on: {e}"));
            if protect.kill_switch {
                return self.finish(Status::failed(format!("The kill switch couldn't be turned on, so nothing was connected. ({e})")));
            }
        }
        match self.start(&cfg) {
            Ok(()) => {
                save_plan(protect, &cfg.addresses, endpoint);
                *self.started.lock().unwrap() = Some(now_seconds());
                log_event("connect: tunnel started, waiting for the server");
                self.status()
            }
            Err(e) => {
                log_event(&format!("connect: failed: {e}"));
                remove_conf();
                if let Ok(scm) = manager() {
                    if let Some(service) = open_service(&scm, TUNNEL_SERVICE) {
                        stop_and_wait(&service, Duration::from_secs(5)); // it may be what failed to stop: this was already waited for
                    }
                }
                self.release("the connection failed");
                *self.error.lock().unwrap() = Some(e.clone());
                self.finish(Status::failed(e))
            }
        }
    }

    /// Every answer says what the service can do and whether the kill switch is blocking.
    pub fn finish(&self, mut status: Status) -> Status {
        status.kill_switch = self.kill_active();
        status.strict = self.strict.load(Ordering::SeqCst);
        status.features = protocol::FEATURES.iter().map(|f| f.to_string()).collect();
        status.version = Some(crate::update::running_version().to_string());
        status
    }

    /// Every few seconds: brings back a tunnel that stopped by itself while the kill switch holds, and lets go once it can't be brought
    /// back. Without a kill switch, the DNS block never outlives the tunnel.
    fn watch(&self) {
        let Ok(_one_at_a_time) = self.changing.try_lock() else { return };
        if self.resuming.load(Ordering::SeqCst) {
            return;
        }
        if !conf_path().map(|p| p.exists()).unwrap_or(false) {
            let paused = *self.paused_at.lock().unwrap();
            if self.kill_active() && paused.is_some_and(|at| now_seconds().saturating_sub(at) < protocol::PAUSE_LIMIT_SECONDS) {
                return; // switching servers: the connect is on its way
            }
            if paused.is_some() {
                log_event("pause: no connect followed");
            }
            *self.paused_at.lock().unwrap() = None;
            // nothing is wanted, so nothing may hold the routes: a tunnel still running here (one that wouldn't stop in time) is stopped
            if let Ok(scm) = manager() {
                if let Some(service) = open_service(&scm, TUNNEL_SERVICE) {
                    if matches!(service_state(&service), ServiceState::Running | ServiceState::Starting) {
                        log_event("tunnel: running though none is wanted, stopping it");
                        stop_and_wait(&service, Duration::from_secs(4));
                    }
                }
            }
            self.release("no tunnel is wanted");
            return;
        }
        let Ok(scm) = manager() else { return };
        let Some(service) = open_service(&scm, TUNNEL_SERVICE) else { return };
        let state = service_state(&service);
        let now = now_seconds();
        if state == ServiceState::Running {
            prefer_tunnel(); // a tunnel restarted here or brought back after a reboot gets the same preference
        }
        if state == ServiceState::Running && matches!(peer_stats(), Some((Some(age), _, _)) if age <= protocol::HANDSHAKE_FRESH_SECONDS) {
            self.ever_connected.store(true, Ordering::SeqCst);
            *self.restarts.lock().unwrap() = (0, 0);
            *self.resumed_at.lock().unwrap() = None;
        }
        let kill = self.kill_active();
        let restarts = self.restarts.lock().unwrap().0;
        let resumed_at = *self.resumed_at.lock().unwrap();
        if kill && protocol::recovery_given_up(restarts, resumed_at, self.ever_connected.load(Ordering::SeqCst), now) {
            log_event("guard: the tunnel can't be brought back, releasing the kill switch");
            drop(service);
            drop(scm);
            let _ = self.disconnect_inner();
            *self.started.lock().unwrap() = None;
            *self.since.lock().unwrap() = None;
            *self.resumed_at.lock().unwrap() = None;
            // the strict kill switch's persistent filters stay (audit M3): only Disconnect or `unblock` removes them
            let message = if self.strict.load(Ordering::SeqCst) { protocol::STRICT_HELD } else { protocol::RELEASED };
            *self.error.lock().unwrap() = Some(message.to_string());
            return;
        }
        if state == ServiceState::Stopped {
            if kill {
                let mut r = self.restarts.lock().unwrap();
                if now.saturating_sub(r.1) >= protocol::RESTART_EVERY_SECONDS {
                    r.0 += 1;
                    r.1 = now;
                    log_event(&format!("tunnel: stopped by itself (Windows exit code {}), starting it again under the kill switch, try {}", exit_code(&service), r.0));
                    unsafe { StartServiceW(service.0, 0, null()) };
                }
            } else {
                self.release("the tunnel stopped");
            }
        }
    }

    /// True once there's nothing more to do: nothing to resume, or the tunnel is running.
    fn try_resume(&self) -> bool {
        let _one_at_a_time = self.changing.lock().unwrap();
        let Ok(path) = conf_path() else { return true };
        if !path.exists() {
            return true;
        }
        let Ok(scm) = manager() else { return false };
        let Ok(service) = tunnel_service(&scm, &path.display().to_string()) else { return false };
        if matches!(service_state(&service), ServiceState::Running | ServiceState::Starting) {
            return true;
        }
        unsafe { StartServiceW(service.0, 0, null()) != 0 || GetLastError() == ERROR_SERVICE_ALREADY_RUNNING }
    }

    fn start(&self, cfg: &config::Config) -> Result<(), String> {
        // the old tunnel always goes first, whether it's switching server or an earlier one never fully closed
        remove_conf();
        if let Some(old) = open_service(&manager()?, TUNNEL_SERVICE) {
            if !stop_and_wait(&old, STOP_LIMIT) {
                return Err("The previous connection is still closing. Please try again in a moment.".into());
            }
        }
        wait_for_adapter_to_go(ADAPTER_LIMIT);
        let dir = tunnels_dir()?;
        let sd = Descriptor::new(PRIVATE_SDDL)?;
        let attrs = sd.attributes();
        let made = unsafe { CreateDirectoryW(wide(&dir.display().to_string()).as_ptr(), &attrs) };
        if made == 0 && unsafe { GetLastError() } != ERROR_ALREADY_EXISTS {
            return Err(format!("Can't create the tunnel folder: {}", last_error()));
        }
        let path = conf_path()?;
        let text = cfg.render();
        let wpath = wide(&path.display().to_string());
        let handle = unsafe { CreateFileW(wpath.as_ptr(), GENERIC_WRITE, 0, &attrs, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, null_mut()) };
        if handle == INVALID_HANDLE_VALUE {
            return Err(format!("Can't save the tunnel settings: {}", last_error()));
        }
        let mut written = 0u32;
        let ok = unsafe { WriteFile(handle, text.as_ptr(), text.len() as u32, &mut written, null_mut()) };
        unsafe { CloseHandle(handle) };
        if ok == 0 || written as usize != text.len() {
            return Err("Can't save the tunnel settings".into());
        }
        let scm = manager()?;
        let service = tunnel_service(&scm, &path.display().to_string())?;
        if unsafe { StartServiceW(service.0, 0, null()) } == 0 {
            return Err(format!("Can't start the tunnel: {}", last_error()));
        }
        // the adapter appears as the tunnel's process starts; it's made Windows' first choice as soon as it's there
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(8) && !prefer_tunnel() {
            std::thread::sleep(Duration::from_millis(150));
        }
        Ok(())
    }

    /// The private key goes first, so it's gone however long the tunnel takes to close. Only read when a tunnel starts, so the running one doesn't need it. If the tunnel is slow to stop,
    /// the status reads as stopping until it has, and the next Connect waits for it.
    fn disconnect_inner(&self) -> Result<(), String> {
        self.close_tunnel()?;
        self.release("the tunnel was closed"); // only once it has stopped, so nothing leaks while it closes
        Ok(())
    }

    /// Closes the tunnel, keeping the kill switch on, so the app can fetch another server's settings. See `Request::Pause`.
    pub fn pause(&self) -> Status {
        let _one_at_a_time = self.changing.lock().unwrap();
        *self.error.lock().unwrap() = None;
        *self.since.lock().unwrap() = None;
        *self.started.lock().unwrap() = None;
        let kill = self.kill_active();
        log_event(&format!("pause: closing the tunnel to switch servers, kill switch {kill}"));
        if kill {
            *self.paused_at.lock().unwrap() = Some(now_seconds());
        }
        if let Err(e) = self.close_tunnel() {
            log_event(&format!("pause: failed: {e}"));
        }
        if !kill {
            self.release("paused");
        }
        self.status()
    }

    fn close_tunnel(&self) -> Result<(), String> {
        remove_conf();
        let scm = manager()?;
        if let Some(service) = open_service(&scm, TUNNEL_SERVICE) {
            if stop_and_wait(&service, STOP_LIMIT) {
                wait_for_adapter_to_go(ADAPTER_LIMIT);
            }
        }
        Ok(())
    }

    pub fn disconnect(&self) -> Status {
        let _one_at_a_time = self.changing.lock().unwrap();
        *self.error.lock().unwrap() = None;
        *self.since.lock().unwrap() = None;
        *self.started.lock().unwrap() = None;
        *self.paused_at.lock().unwrap() = None;
        log_event("disconnect: requested");
        let closed = self.disconnect_inner();
        self.remove_strict("Disconnect");
        match closed {
            Ok(()) => {
                let status = self.status();
                log_event(&format!("disconnect: done, state {:?}", status.state));
                status
            }
            Err(e) => {
                log_event(&format!("disconnect: failed: {e}"));
                self.release("disconnect");
                self.finish(Status::failed(e))
            }
        }
    }

    pub fn status(&self) -> Status {
        let status = self.status_inner();
        self.finish(status)
    }

    fn status_inner(&self) -> Status {
        if let Some(e) = self.error.lock().unwrap().clone() {
            return Status::failed(e);
        }
        let Ok(scm) = manager() else { return Status::failed("Can't read the tunnel's state") };
        let Some(service) = open_service(&scm, TUNNEL_SERVICE) else { return Status::off() };
        let state = service_state(&service);
        let busy = self.changing.try_lock().is_err() || self.resuming.load(Ordering::SeqCst);
        let wanted = conf_path().map(|p| p.exists()).unwrap_or(false);
        if protocol::tunnel_ended(state, wanted, busy) && self.kill_active() {
            // the watchdog is bringing it back, with everything blocked meanwhile
            return protocol::decide(ServiceState::Starting, None, 0, 0);
        }
        if protocol::tunnel_ended(state, wanted, busy) {
            *self.since.lock().unwrap() = None;
            let code = exit_code(&service);
            // once per stop, not on every status check
            if self.started.lock().unwrap().take().is_some() {
                log_event(&format!("tunnel: stopped by itself, Windows exit code {code}"));
            }
            return Status::failed(protocol::ended_message(code));
        }
        let (age, rx, tx) = if state == ServiceState::Running { peer_stats().unwrap_or((None, 0, 0)) } else { (None, 0, 0) };
        let mut status = protocol::decide(state, age, rx, tx);
        let now = now_seconds();
        {
            let mut since = self.since.lock().unwrap();
            *since = protocol::session_since(*since, &status, now);
            status.connected_since = *since;
        }
        let started = *self.started.lock().unwrap();
        if protocol::connect_timed_out(started, &status, now) {
            drop(service);
            drop(scm);
            // a connect or disconnect already under way decides for itself
            if let Ok(_one_at_a_time) = self.changing.try_lock() {
                log_event("connect: no answer from the server in time, closing");
                let _ = self.disconnect_inner();
                *self.started.lock().unwrap() = None;
                *self.since.lock().unwrap() = None;
                *self.error.lock().unwrap() = Some(protocol::NO_ANSWER.to_string());
                return Status::failed(protocol::NO_ANSWER);
            }
            return status;
        }
        status
    }
}

impl Default for Tunnel {
    fn default() -> Self {
        Self::new()
    }
}

/// The tunnel service's own process: hands over to WireGuard's tunnel.dll, which runs until the service stops.
pub fn run_tunnel(conf: &str) -> Result<(), String> {
    let lib = unsafe { LoadLibraryW(wide(&exe_dir()?.join("tunnel.dll").display().to_string()).as_ptr()) };
    if lib.is_null() {
        return Err(format!("tunnel.dll: {}", last_error()));
    }
    let f = unsafe { GetProcAddress(lib, c"WireGuardTunnelService".as_ptr() as *const u8) }.ok_or("tunnel.dll has no entry point")?;
    let run: unsafe extern "C" fn(*const u16) -> u8 = unsafe { std::mem::transmute(f) };
    if unsafe { run(wide(conf).as_ptr()) } == 0 {
        return Err("the tunnel stopped with an error".into());
    }
    Ok(())
}

// --- statistics, through the driver's published API ----------------------------------------------------------------

#[repr(C, align(8))]
struct WgInterface {
    flags: u32,
    listen_port: u16,
    private_key: [u8; 32],
    public_key: [u8; 32],
    peers_count: u32,
}

#[repr(C, align(8))]
struct WgPeer {
    flags: u32,
    reserved: u32,
    public_key: [u8; 32],
    preshared_key: [u8; 32],
    keepalive: u16,
    endpoint: [u32; 7],
    tx: u64,
    rx: u64,
    last_handshake: u64,
    allowed_ips: u32,
}

const WG_ALLOWED_IP_SIZE: usize = 24;
const UNIX_TO_1601_100NS: u64 = 116_444_736_000_000_000;

/// (seconds since the last handshake, bytes in, bytes out), for the tunnel's only peer. The private key in the answer is wiped at once.
fn peer_stats() -> Option<(Option<u64>, u64, u64)> {
    type Open = unsafe extern "system" fn(*const u16) -> *mut c_void;
    type Close = unsafe extern "system" fn(*mut c_void);
    type Get = unsafe extern "system" fn(*mut c_void, *mut u8, *mut u32) -> i32;
    let dll = wide(&exe_dir().ok()?.join("wireguard.dll").display().to_string());
    unsafe {
        let lib = LoadLibraryW(dll.as_ptr());
        if lib.is_null() {
            return None;
        }
        let open: Open = std::mem::transmute(GetProcAddress(lib, c"WireGuardOpenAdapter".as_ptr() as *const u8)?);
        let close: Close = std::mem::transmute(GetProcAddress(lib, c"WireGuardCloseAdapter".as_ptr() as *const u8)?);
        let get: Get = std::mem::transmute(GetProcAddress(lib, c"WireGuardGetConfiguration".as_ptr() as *const u8)?);
        let adapter = open(wide(TUNNEL_NAME).as_ptr());
        if adapter.is_null() {
            return None;
        }
        let mut size = 4096u32;
        let mut buf = vec![0u64; size as usize / 8 + 1];
        let mut ok = get(adapter, buf.as_mut_ptr() as *mut u8, &mut size);
        if ok == 0 && GetLastError() == ERROR_MORE_DATA {
            buf = vec![0u64; size as usize / 8 + 1];
            ok = get(adapter, buf.as_mut_ptr() as *mut u8, &mut size);
        }
        close(adapter);
        let mut out = None;
        if ok != 0 {
            let iface = &*(buf.as_ptr() as *const WgInterface);
            if iface.peers_count >= 1 {
                let peer = &*((buf.as_ptr() as *const u8).add(size_of::<WgInterface>()) as *const WgPeer);
                let _ = WG_ALLOWED_IP_SIZE; // one peer is all a Kiwi tunnel has, so nothing after it is read
                let age = (peer.last_handshake != 0).then(|| {
                    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
                    now.saturating_sub(peer.last_handshake.saturating_sub(UNIX_TO_1601_100NS) / 10_000_000)
                });
                out = Some((age, peer.rx, peer.tx));
            }
        }
        std::ptr::write_bytes(buf.as_mut_ptr(), 0, buf.len());
        out
    }
}

// --- the pipe -----------------------------------------------------------------------------------------------------

fn read_line(pipe: HANDLE) -> Result<String, String> {
    let mut data = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        let mut got = 0u32;
        if unsafe { ReadFile(pipe, chunk.as_mut_ptr(), chunk.len() as u32, &mut got, null_mut()) } == 0 || got == 0 {
            return Err("the client went away".into());
        }
        data.extend_from_slice(&chunk[..got as usize]);
        if data.len() > protocol::MAX_REQUEST {
            return Err("request too large".into());
        }
        if data.last() == Some(&b'\n') {
            return String::from_utf8(data).map_err(|_| "request not understood".to_string());
        }
    }
}

fn write_all(pipe: HANDLE, bytes: &[u8]) -> bool {
    let mut written = 0u32;
    unsafe { WriteFile(pipe, bytes.as_ptr(), bytes.len() as u32, &mut written, null_mut()) != 0 }
}

/// Works out who's on the other end from Windows' own records. Anything it can't establish counts as Other.
fn caller_of(pipe: HANDLE) -> Caller {
    unsafe {
        let mut pid = 0u32;
        if GetNamedPipeClientProcessId(pipe, &mut pid) == 0 {
            return Caller::Other;
        }
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if process.is_null() {
            return Caller::Other;
        }
        let mut elevated = false;
        let mut token: HANDLE = null_mut();
        if OpenProcessToken(process, TOKEN_QUERY, &mut token) != 0 {
            let mut info: TOKEN_ELEVATION = zeroed();
            let mut got = 0u32;
            elevated = GetTokenInformation(token, TokenElevation, &mut info as *mut _ as *mut c_void, size_of::<TOKEN_ELEVATION>() as u32, &mut got) != 0 && info.TokenIsElevated != 0;
            CloseHandle(token);
        }
        let mut path = [0u16; 1024];
        let mut len = path.len() as u32;
        let named = QueryFullProcessImageNameW(process, 0, path.as_mut_ptr(), &mut len) != 0;
        CloseHandle(process);
        if elevated {
            return Caller::Elevated;
        }
        let image = if named { String::from_utf16_lossy(&path[..len as usize]) } else { return Caller::Other };
        match exe_dir() {
            Ok(dir) if protocol::is_app_image(&image, &dir.display().to_string(), APP_EXE) => Caller::App,
            _ => Caller::Other,
        }
    }
}

fn serve_client(pipe: HANDLE, tunnel: &Tunnel, updater: &Arc<Updater>, check_callers: bool) {
    let request = read_line(pipe).and_then(|l| protocol::parse_request(l.trim_end())).and_then(|r| {
        if !check_callers || protocol::allowed(&r, if matches!(r, Request::Status) { Caller::Other } else { caller_of(pipe) }) {
            Ok(r)
        } else {
            Err(protocol::NOT_ALLOWED.to_string())
        }
    });
    let mut status = match request {
        Ok(Request::Connect { config, protect }) => tunnel.connect(&config, &protect),
        Ok(Request::Disconnect) => tunnel.disconnect(),
        Ok(Request::Pause) => tunnel.pause(),
        Ok(Request::Status) => tunnel.status(),
        Ok(Request::UpdateCheck) => {
            updater.check(true);
            tunnel.status()
        }
        Ok(Request::UpdateInstall) => {
            // the download can take a while: it runs on its own, and the answer says "downloading"
            let u = Arc::clone(updater);
            std::thread::spawn(move || {
                let _ = u.install(true);
            });
            std::thread::sleep(Duration::from_millis(200));
            tunnel.status()
        }
        Err(e) => tunnel.finish(Status::failed(e)),
    };
    status.update = Some(updater.info());
    if let Ok(mut line) = serde_json::to_vec(&status) {
        line.push(b'\n');
        write_all(pipe, &line);
    }
}

/// Serves the app until `stop` is set. The first instance claims the name, and PIPE_SDDL stops anyone without administrator rights adding an instance of their own.
pub fn serve(stop: Arc<std::sync::atomic::AtomicBool>) -> Result<(), String> {
    serve_with(stop, Some(PIPE_SDDL))
}

/// For tests: one instance under the service's real rule, so they can try the rule itself.
#[doc(hidden)]
pub fn pipe_under_real_rule_for_tests() -> Result<HANDLE, String> {
    let sd = Descriptor::new(PIPE_SDDL)?;
    let attrs = sd.attributes();
    let pipe = unsafe { CreateNamedPipeW(wide(PIPE_NAME).as_ptr(), PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, &attrs) };
    if pipe == INVALID_HANDLE_VALUE {
        return Err(last_error());
    }
    Ok(pipe)
}

/// For tests, which run the same loop without being SYSTEM: PIPE_SDDL would refuse them their own second instance. Windows' default security applies instead.
#[doc(hidden)]
pub fn serve_for_tests(stop: Arc<std::sync::atomic::AtomicBool>) -> Result<(), String> {
    serve_with(stop, None)
}

fn serve_with(stop: Arc<std::sync::atomic::AtomicBool>, sddl: Option<&str>) -> Result<(), String> {
    let sd = sddl.map(Descriptor::new).transpose()?;
    let check_callers = sddl.is_some();
    let active = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let tunnel = Arc::new(Tunnel::new());
    let updater = Arc::new(Updater::new());
    if check_callers {
        if crate::firewall::strict_present() {
            tunnel.strict.store(true, Ordering::SeqCst);
            log_event("guard: the strict kill switch is in place from before this start");
        }
        update_win::after_start(&updater);
        let checking = Arc::clone(&updater);
        let watched_tunnel = Arc::clone(&tunnel);
        std::thread::spawn(move || update_loop(checking, watched_tunnel));
        let resuming = Arc::clone(&tunnel);
        std::thread::spawn(move || resume_after_restart(resuming));
        let watched = Arc::clone(&tunnel);
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_secs(5));
            watched.watch();
        });
    }
    let mut first = FILE_FLAG_FIRST_PIPE_INSTANCE;
    while !stop.load(std::sync::atomic::Ordering::SeqCst) {
        let attrs = sd.as_ref().map(Descriptor::attributes);
        let attrs_ptr = attrs.as_ref().map_or(null(), |a| a as *const SECURITY_ATTRIBUTES);
        let pipe = unsafe {
            CreateNamedPipeW(wide(PIPE_NAME).as_ptr(), PIPE_ACCESS_DUPLEX | first, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, attrs_ptr)
        };
        if pipe == INVALID_HANDLE_VALUE {
            return Err(format!("Can't open the pipe: {}", last_error()));
        }
        first = 0;
        let connected = unsafe { ConnectNamedPipe(pipe, null_mut()) } != 0 || unsafe { GetLastError() } == ERROR_PIPE_CONNECTED;
        if stop.load(std::sync::atomic::Ordering::SeqCst) {
            unsafe { CloseHandle(pipe) };
            break;
        }
        if !connected {
            unsafe { CloseHandle(pipe) };
            continue;
        }
        if active.load(std::sync::atomic::Ordering::SeqCst) >= MAX_CLIENTS {
            unsafe { CloseHandle(pipe) };
            std::thread::sleep(Duration::from_millis(50));
            continue;
        }
        active.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let active = Arc::clone(&active);
        let tunnel = Arc::clone(&tunnel);
        let updater = Arc::clone(&updater);
        let raw = pipe as usize;
        std::thread::spawn(move || {
            let pipe = raw as HANDLE;
            serve_client(pipe, &tunnel, &updater, check_callers);
            unsafe {
                FlushFileBuffers(pipe);
                CloseHandle(pipe); // closing, not DisconnectNamedPipe: that makes the client's read fail after it already has its answer
            }
            active.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
        });
    }
    Ok(())
}

/// Checks for updates two minutes after start, then every 6 hours (plus up to an hour, so installs don't all ask at once). A
/// security or required update installs by itself only while no tunnel is wanted; anything else waits for the person's click.
fn update_loop(updater: Arc<Updater>, tunnel: Arc<Tunnel>) {
    use std::hash::{BuildHasher, Hasher};
    std::thread::sleep(Duration::from_secs(120));
    loop {
        let info = updater.check(false);
        let idle = !conf_path().map(|p| p.exists()).unwrap_or(false) && !tunnel.strict_held();
        if matches!(info.state, crate::update::UpdateState::Available | crate::update::UpdateState::Required | crate::update::UpdateState::Failed)
            && updater.wants_silent_install()
            && idle
            && updater.may_retry()
        {
            log_event("update: installing a security update while no tunnel is wanted");
            let _ = updater.install(false);
        }
        let jitter = std::collections::hash_map::RandomState::new().build_hasher().finish() % 3600;
        std::thread::sleep(Duration::from_secs(6 * 3600 + jitter));
    }
}

/// True while a tunnel is wanted (its config exists until Disconnect).
pub fn tunnel_wanted() -> bool {
    conf_path().map(|p| p.exists()).unwrap_or(false)
}

/// Wakes `serve` so it can notice it's been told to stop.
pub fn nudge_pipe() {
    let _ = open_once();
}

/// Asks only for PIPE_CLIENT_ACCESS. Anonymous: whatever is on the other end can't act as this person, even if it isn't the service.
fn open_once() -> std::io::Result<std::fs::File> {
    std::fs::OpenOptions::new().access_mode(PIPE_CLIENT_ACCESS).security_qos_flags(SECURITY_ANONYMOUS).open(PIPE_NAME)
}

/// Opens the pipe, allowing for the instant between one client finishing and the next instance opening, so that gap never reads as "the service isn't running".
pub fn open_pipe() -> std::io::Result<std::fs::File> {
    let mut tries = 0;
    loop {
        match open_once() {
            Err(e) if tries < 10 && matches!(e.raw_os_error().map(|c| c as u32), Some(ERROR_PIPE_BUSY | ERROR_FILE_NOT_FOUND)) => {
                tries += 1;
                std::thread::sleep(Duration::from_millis(100));
            }
            result => return result,
        }
    }
}

/// One request over an open pipe.
fn exchange(mut pipe: std::fs::File, request: &Request) -> Result<Status, String> {
    let mut line = serde_json::to_vec(request).map_err(|e| e.to_string())?;
    line.push(b'\n');
    pipe.write_all(&line).map_err(|e| e.to_string())?;
    let mut text = String::new();
    std::io::BufRead::read_line(&mut std::io::BufReader::new(pipe), &mut text).map_err(|e| e.to_string())?;
    serde_json::from_str(text.trim()).map_err(|_| "The service gave an answer I can't read".to_string())
}

/// Sends without checking who's listening, so only tests may use it: they run the loop themselves, and it's how the fake service test proves it could have caught something.
#[doc(hidden)]
pub fn ask_unverified(request: &Request) -> Result<Status, String> {
    exchange(open_pipe().map_err(|_| "The Kiwi VPN service isn't running".to_string())?, request)
}

#[derive(Debug, PartialEq)]
pub enum AskError {
    /// The pipe isn't there: the service isn't installed or isn't running.
    NoService,
    /// Something other than the service is on the pipe. Nothing was sent to it.
    NotTheService,
    Broken(String),
}

impl AskError {
    pub fn message(&self) -> String {
        match self {
            AskError::NoService => "The Kiwi VPN service isn't running.".into(),
            AskError::NotTheService => "Something other than the Kiwi VPN service is answering. Nothing was sent to it.".into(),
            AskError::Broken(e) => format!("Couldn't talk to the Kiwi VPN service: {e}"),
        }
    }
}

/// The service's identity, from what a program without administrator rights can't fake: a pipe owned by SYSTEM or Administrators, whose server runs in session 0,
/// where only services run. The session also catches a program that's running elevated, whose pipes Administrators own.
pub fn trusted_server(owner_sid: &str, server_session: u32) -> bool {
    (owner_sid == "S-1-5-18" || owner_sid == "S-1-5-32-544") && server_session == 0
}

fn pipe_owner(pipe: &std::fs::File) -> Result<String, String> {
    unsafe {
        let mut owner: *mut c_void = null_mut();
        let mut sd: PSECURITY_DESCRIPTOR = null_mut();
        let got = GetSecurityInfo(pipe.as_raw_handle() as _, SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION, &mut owner as *mut _ as _, null_mut(), null_mut(), null_mut(), &mut sd);
        if got != ERROR_SUCCESS {
            return Err(format!("Windows error {got}"));
        }
        let mut text: *mut u16 = null_mut();
        let result = if ConvertSidToStringSidW(owner as _, &mut text) == 0 {
            Err(last_error())
        } else {
            let len = (0..).take_while(|&i| *text.add(i) != 0).count();
            let sid = String::from_utf16_lossy(std::slice::from_raw_parts(text, len));
            LocalFree(text as _);
            Ok(sid)
        };
        LocalFree(sd as _);
        result
    }
}

fn server_session(pipe: &std::fs::File) -> Result<u32, String> {
    let mut session = u32::MAX;
    if unsafe { GetNamedPipeServerSessionId(pipe.as_raw_handle() as _, &mut session) } == 0 {
        return Err(last_error());
    }
    Ok(session)
}

/// One request to the real service. Nothing is written until the other end has proved it's the service, because a connect request carries the device's private key.
pub fn ask_service(request: &Request) -> Result<Status, AskError> {
    let pipe = open_pipe().map_err(|e| match e.raw_os_error().map(|c| c as u32) {
        Some(ERROR_FILE_NOT_FOUND | ERROR_PIPE_BUSY) => AskError::NoService,
        _ => AskError::Broken(e.to_string()),
    })?;
    let owner = pipe_owner(&pipe).map_err(AskError::Broken)?;
    let session = server_session(&pipe).map_err(AskError::Broken)?;
    if !trusted_server(&owner, session) {
        return Err(AskError::NotTheService);
    }
    exchange(pipe, request).map_err(AskError::Broken)
}

// --- install and uninstall ----------------------------------------------------------------------------------------

/// The service runs as SYSTEM from wherever it's installed, so that folder has to be one only administrators can write to.
pub fn in_protected_folder(exe: &str, program_files: &str) -> bool {
    let (exe, root) = (exe.to_ascii_lowercase(), format!("{}\\", program_files.trim_end_matches('\\').to_ascii_lowercase()));
    !program_files.is_empty() && exe.starts_with(&root)
}

pub fn install() -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    if !in_protected_folder(&exe.display().to_string(), &std::env::var("ProgramFiles").unwrap_or_default()) {
        return Err("The Kiwi VPN service must be installed under Program Files.".into());
    }
    let scm = manager()?;
    let command = format!("\"{}\" service", exe.display());
    if open_service(&scm, OWN_SERVICE).is_some() {
        return Ok(());
    }
    let deps: Vec<u16> = "Nsi\0TcpIp\0\0".encode_utf16().collect();
    let h = unsafe {
        CreateServiceW(scm.0, wide(OWN_SERVICE).as_ptr(), wide("Kiwi VPN").as_ptr(), SERVICE_ALL_ACCESS, SERVICE_WIN32_OWN_PROCESS, SERVICE_AUTO_START, SERVICE_ERROR_NORMAL,
                       wide(&command).as_ptr(), null(), null_mut(), deps.as_ptr(), null(), null())
    };
    if h.is_null() {
        return Err(format!("Can't install the service: {}", last_error()));
    }
    let service = Sc(h);
    // if the service ever crashes, Windows starts it again, and it brings the wanted tunnel and its kill switch back
    let mut actions = [SC_ACTION { Type: SC_ACTION_RESTART, Delay: 3000 }, SC_ACTION { Type: SC_ACTION_RESTART, Delay: 10_000 }, SC_ACTION { Type: SC_ACTION_RESTART, Delay: 30_000 }];
    let mut failure = SERVICE_FAILURE_ACTIONSW { dwResetPeriod: 86_400, lpRebootMsg: null_mut(), lpCommand: null_mut(), cActions: actions.len() as u32, lpsaActions: actions.as_mut_ptr() };
    unsafe { ChangeServiceConfig2W(service.0, SERVICE_CONFIG_FAILURE_ACTIONS, &mut failure as *mut _ as *mut c_void) };
    if unsafe { StartServiceW(service.0, 0, null()) } == 0 {
        return Err(format!("Can't start the service: {}", last_error()));
    }
    Ok(())
}

pub fn uninstall() -> Result<(), String> {
    // a removed Kiwi must never leave the PC blocked: the strict kill switch's persistent filters go too
    let _ = crate::firewall::strict_remove();
    stop_and_delete(TUNNEL_SERVICE)?;
    if let Ok(dir) = tunnels_dir() {
        let _ = std::fs::remove_dir_all(dir);
    }
    nudge_pipe();
    stop_and_delete(OWN_SERVICE)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const FILE_CREATE_PIPE_INSTANCE: u32 = 0x4;

    #[test]
    fn people_can_use_the_pipe_but_not_add_to_it() {
        assert_eq!(PIPE_CLIENT_ACCESS & FILE_CREATE_PIPE_INSTANCE, 0);
        assert!(PIPE_SDDL.ends_with(&format!("(A;;{PIPE_CLIENT_ACCESS:#x};;;IU)")), "the pipe's rule and the client's request must match");
        assert!(!PIPE_SDDL.contains("GW"), "generic write includes creating instances");
    }

    #[test]
    fn the_service_only_installs_where_users_cant_write() {
        let root = r"C:\Program Files";
        assert!(in_protected_folder(r"C:\Program Files\KiwiVPN\kiwivpn-service.exe", root));
        assert!(in_protected_folder(r"c:\program files\kiwivpn\kiwivpn-service.exe", root));
        assert!(!in_protected_folder(r"C:\Users\me\kiwivpn-service.exe", root));
        assert!(!in_protected_folder(r"C:\Program Files Evil\kiwivpn-service.exe", root));
        assert!(!in_protected_folder(r"C:\Users\me\kiwivpn-service.exe", ""));
    }

    #[test]
    fn only_a_system_or_administrators_pipe_in_session_0_is_the_service() {
        assert!(trusted_server("S-1-5-18", 0));
        assert!(trusted_server("S-1-5-32-544", 0));
        assert!(!trusted_server("S-1-5-32-544", 1), "an elevated program in someone's session");
        assert!(!trusted_server("S-1-5-18", 1));
        for other in ["S-1-5-11", "S-1-5-21-1-2-3-1001", "S-1-1-0", ""] {
            assert!(!trusted_server(other, 0), "{other}");
        }
    }
}
