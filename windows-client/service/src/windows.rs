//! Everything that touches Windows: the tunnel service, the driver's statistics, and the named pipe the app talks through.

use crate::config;
use crate::protocol::{self, Caller, Request, ServiceState, Status, PIPE_NAME};
use std::ffi::c_void;
use std::io::Write;
use std::mem::{size_of, zeroed};
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::AsRawHandle;
use std::path::PathBuf;
use std::ptr::{null, null_mut};
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
const PRIVATE_SDDL: &str = "D:P(A;OICI;GA;;;SY)(A;OICI;GA;;;BA)";
/// What the signed-in person may do on the pipe: read data, write data, read its attributes and owner, wait on it. Exactly this, because for a pipe the "append data" part of
/// generic write is also the right to create another instance of it, which would let any program of theirs pose as the service on the service's own pipe, under its owner.
pub const PIPE_CLIENT_ACCESS: u32 = 0x0012_0083;
/// SYSTEM fully, the signed-in person PIPE_CLIENT_ACCESS. Nobody else, nothing over the network (see PIPE_REJECT_REMOTE_CLIENTS).
const PIPE_SDDL: &str = "D:P(A;;GA;;;SY)(A;;0x120083;;;IU)";
const SERVICE_SID_TYPE_UNRESTRICTED: u32 = 1;

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

pub fn last_error() -> String {
    format!("Windows error {}", unsafe { GetLastError() })
}

fn exe_dir() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    exe.parent().map(PathBuf::from).ok_or_else(|| "no install folder".to_string())
}

/// Under the install folder, which only administrators can write to.
fn tunnels_dir() -> Result<PathBuf, String> {
    Ok(exe_dir()?.join("tunnels"))
}

fn conf_path() -> Result<PathBuf, String> {
    Ok(tunnels_dir()?.join(format!("{TUNNEL_NAME}.conf")))
}

struct Descriptor(PSECURITY_DESCRIPTOR);

impl Descriptor {
    fn new(sddl: &str) -> Result<Self, String> {
        let mut sd: PSECURITY_DESCRIPTOR = null_mut();
        let ok = unsafe { ConvertStringSecurityDescriptorToSecurityDescriptorW(wide(sddl).as_ptr(), SDDL_REVISION_1, &mut sd, null_mut()) };
        if ok == 0 {
            return Err(last_error());
        }
        Ok(Descriptor(sd))
    }
    fn attributes(&self) -> SECURITY_ATTRIBUTES {
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
    let mut s: SERVICE_STATUS = unsafe { zeroed() };
    unsafe { ControlService(service.0, SERVICE_CONTROL_STOP, &mut s) };
    let start = Instant::now();
    while service_state(&service) != ServiceState::Stopped && service_state(&service) != ServiceState::Missing && start.elapsed() < Duration::from_secs(20) {
        std::thread::sleep(Duration::from_millis(200));
    }
    unsafe { DeleteService(service.0) };
    Ok(())
}

fn create_tunnel_service(scm: &Sc, conf: &str) -> Result<Sc, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let command = format!("\"{}\" tunnel \"{}\"", exe.display(), conf);
    let deps: Vec<u16> = "Nsi\0TcpIp\0\0".encode_utf16().collect();
    let start = Instant::now();
    let h = loop {
        let h = unsafe {
            CreateServiceW(scm.0, wide(TUNNEL_SERVICE).as_ptr(), wide("Kiwi VPN tunnel").as_ptr(), SERVICE_ALL_ACCESS, SERVICE_WIN32_OWN_PROCESS, SERVICE_DEMAND_START, SERVICE_ERROR_NORMAL,
                           wide(&command).as_ptr(), null(), null_mut(), deps.as_ptr(), null(), null())
        };
        // switching server: Windows finishes deleting the old tunnel only once every handle to it has closed, such as a status check running right now
        if !h.is_null() || unsafe { GetLastError() } != ERROR_SERVICE_MARKED_FOR_DELETE || start.elapsed() > Duration::from_secs(10) {
            break h;
        }
        std::thread::sleep(Duration::from_millis(200));
    };
    if h.is_null() {
        return Err(format!("Can't create the tunnel: {}", last_error()));
    }
    let service = Sc(h);
    // WireGuard's documentation calls this essential: the driver's firewall rules depend on the service's SID.
    let mut sid = SERVICE_SID_INFO { dwServiceSidType: SERVICE_SID_TYPE_UNRESTRICTED };
    if unsafe { ChangeServiceConfig2W(service.0, SERVICE_CONFIG_SERVICE_SID_INFO, &mut sid as *mut _ as *mut c_void) } == 0 {
        return Err(format!("Can't configure the tunnel: {}", last_error()));
    }
    Ok(service)
}

pub struct Tunnel {
    error: Mutex<Option<String>>,
    since: Mutex<Option<u64>>,
    /// One connect or disconnect at a time: two running together could delete each other's tunnel.
    changing: Mutex<()>,
}

impl Tunnel {
    pub fn new() -> Self {
        Tunnel { error: Mutex::new(None), since: Mutex::new(None), changing: Mutex::new(()) }
    }

    pub fn connect(&self, text: &str) -> Status {
        let _one_at_a_time = self.changing.lock().unwrap();
        *self.error.lock().unwrap() = None;
        *self.since.lock().unwrap() = None;
        let cfg = match config::parse(text) {
            Ok(c) => c,
            Err(e) => return Status::failed(e),
        };
        match self.start(&cfg) {
            Ok(()) => self.status(),
            Err(e) => {
                let _ = self.disconnect_inner();
                *self.error.lock().unwrap() = Some(e.clone());
                Status::failed(e)
            }
        }
    }

    fn start(&self, cfg: &config::Config) -> Result<(), String> {
        stop_and_delete(TUNNEL_SERVICE)?; // switching server: the old tunnel always goes first
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
        let service = create_tunnel_service(&scm, &path.display().to_string())?;
        if unsafe { StartServiceW(service.0, 0, null()) } == 0 {
            return Err(format!("Can't start the tunnel: {}", last_error()));
        }
        Ok(())
    }

    fn disconnect_inner(&self) -> Result<(), String> {
        stop_and_delete(TUNNEL_SERVICE)?;
        if let Ok(path) = conf_path() {
            let _ = std::fs::remove_file(path); // holds the device's private key: never left behind
        }
        Ok(())
    }

    pub fn disconnect(&self) -> Status {
        let _one_at_a_time = self.changing.lock().unwrap();
        *self.error.lock().unwrap() = None;
        *self.since.lock().unwrap() = None;
        match self.disconnect_inner() {
            Ok(()) => self.status(),
            Err(e) => Status::failed(e),
        }
    }

    pub fn status(&self) -> Status {
        if let Some(e) = self.error.lock().unwrap().clone() {
            return Status::failed(e);
        }
        let Ok(scm) = manager() else { return Status::failed("Can't read the tunnel's state") };
        let Some(service) = open_service(&scm, TUNNEL_SERVICE) else { return Status::off() };
        let state = service_state(&service);
        let (age, rx, tx) = if state == ServiceState::Running { peer_stats().unwrap_or((None, 0, 0)) } else { (None, 0, 0) };
        let mut status = protocol::decide(state, age, rx, tx);
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
        let mut since = self.since.lock().unwrap();
        *since = protocol::session_since(*since, &status, now);
        status.connected_since = *since;
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

fn serve_client(pipe: HANDLE, tunnel: &Tunnel, check_callers: bool) {
    let request = read_line(pipe).and_then(|l| protocol::parse_request(l.trim_end())).and_then(|r| {
        if !check_callers || protocol::allowed(&r, if matches!(r, Request::Status) { Caller::Other } else { caller_of(pipe) }) {
            Ok(r)
        } else {
            Err(protocol::NOT_ALLOWED.to_string())
        }
    });
    let status = match request {
        Ok(Request::Connect { config }) => tunnel.connect(&config),
        Ok(Request::Disconnect) => tunnel.disconnect(),
        Ok(Request::Status) => tunnel.status(),
        Err(e) => Status::failed(e),
    };
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
        let raw = pipe as usize;
        std::thread::spawn(move || {
            let pipe = raw as HANDLE;
            serve_client(pipe, &tunnel, check_callers);
            unsafe {
                FlushFileBuffers(pipe);
                CloseHandle(pipe); // closing, not DisconnectNamedPipe: that makes the client's read fail after it already has its answer
            }
            active.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
        });
    }
    Ok(())
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
    if unsafe { StartServiceW(service.0, 0, null()) } == 0 {
        return Err(format!("Can't start the service: {}", last_error()));
    }
    Ok(())
}

pub fn uninstall() -> Result<(), String> {
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
