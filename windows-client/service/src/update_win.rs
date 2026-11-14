//! The Windows half of signed auto-update (docs/AUTO-UPDATE.md, `update` holds the rules): the one HTTPS fetch (WinHTTP, Windows'
//! own certificate checks, no redirects), the protected state under HKLM\SOFTWARE\KiwiVPN\Update (written only by this SYSTEM
//! service), the SYSTEM-only `updates` folder, the Authenticode check by handle, and starting the verified installer.
//!
//! The app can only ask "check" and "install": it never names a URL, a path or a file. Everything here is fixed or comes from a
//! manifest whose signature was checked first.

use crate::update::{self, Download, Health, Offer, Pending, Pin, Refused, Release, Signer, UpdateInfo, UpdateState, Version};
use crate::windows::{log_event, wide, Descriptor, PRIVATE_SDDL};
use std::ffi::c_void;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::mem::size_of;
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use windows_sys::core::GUID;
use windows_sys::Win32::Foundation::{LocalFree, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::Security::Authorization::{ConvertSidToStringSidW, GetNamedSecurityInfoW, SE_FILE_OBJECT};
use windows_sys::Win32::Security::{OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR};
use windows_sys::Win32::Storage::FileSystem::{CreateDirectoryW, FILE_SHARE_READ};
use windows_sys::Win32::System::Registry::{RegCloseKey, RegCreateKeyExW, RegDeleteTreeW, RegDeleteValueW, RegQueryValueExW, RegSetValueExW, HKEY, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WRITE, REG_OPTION_NON_VOLATILE, REG_SZ};

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

// ----- protected state -----------------------------------------------------------------------------------------------

pub const STATE_KEY: &str = r"SOFTWARE\KiwiVPN\Update";
/// SYSTEM and Administrators full control, Users read. Not under the service's own key, which an uninstall deletes on upgrades.
const STATE_SDDL: &str = "D:P(A;OICI;KA;;;SY)(A;OICI;KA;;;BA)(A;OICI;KR;;;BU)";

struct Key(HKEY);

impl Drop for Key {
    fn drop(&mut self) {
        unsafe { RegCloseKey(self.0) };
    }
}

fn state_key() -> Option<Key> {
    let sd = Descriptor::new(STATE_SDDL).ok()?;
    let attrs = sd.attributes();
    let mut key: HKEY = null_mut();
    let got = unsafe { RegCreateKeyExW(HKEY_LOCAL_MACHINE, wide(STATE_KEY).as_ptr(), 0, null(), REG_OPTION_NON_VOLATILE, KEY_READ | KEY_WRITE, &attrs, &mut key, null_mut()) };
    (got == 0).then_some(Key(key))
}

pub fn get(name: &str) -> Option<String> {
    let key = state_key()?;
    let wname = wide(name);
    let mut size = 0u32;
    if unsafe { RegQueryValueExW(key.0, wname.as_ptr(), null(), null_mut(), null_mut(), &mut size) } != 0 || size == 0 || size > 65_536 {
        return None;
    }
    let mut buf = vec![0u16; size as usize / 2 + 1];
    let mut got = (buf.len() * 2) as u32;
    if unsafe { RegQueryValueExW(key.0, wname.as_ptr(), null(), null_mut(), buf.as_mut_ptr() as *mut u8, &mut got) } != 0 {
        return None;
    }
    let len = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    Some(String::from_utf16_lossy(&buf[..len]))
}

pub fn set(name: &str, value: &str) -> Result<(), String> {
    let key = state_key().ok_or("can't open the update state")?;
    let data = wide(value);
    let got = unsafe { RegSetValueExW(key.0, wide(name).as_ptr(), 0, REG_SZ, data.as_ptr() as *const u8, (data.len() * 2) as u32) };
    if got != 0 {
        return Err(format!("can't save the update state (Windows error {got})"));
    }
    Ok(())
}

pub fn clear(name: &str) {
    if let Some(key) = state_key() {
        unsafe { RegDeleteValueW(key.0, wide(name).as_ptr()) };
    }
}

/// A real uninstall removes everything (an administrator action anyway).
pub fn forget_all() {
    unsafe { RegDeleteTreeW(HKEY_LOCAL_MACHINE, wide(STATE_KEY).as_ptr()) };
}

pub struct Store {
    pub highest: Option<Version>,
    pub seq: u64,
    pub revoked: Vec<String>,
    pub pin_add: Vec<Pin>,
    pub install_id: Vec<u8>,
    pub pending: Option<Pending>,
}

fn json<T: serde::de::DeserializeOwned + Default>(name: &str) -> T {
    get(name).and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default()
}

/// 128 bits made once, only for the rollout bucket, never sent anywhere. Seeded from Windows' own randomness (RandomState).
fn new_install_id() -> String {
    use std::hash::{BuildHasher, Hasher};
    let mut out = String::new();
    for _ in 0..2 {
        let mut h = std::collections::hash_map::RandomState::new().build_hasher();
        h.write_u128(SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
        h.write_u32(std::process::id());
        out += &format!("{:016x}", h.finish());
    }
    out
}

pub fn load() -> Store {
    let install_id = match get("InstallId") {
        Some(id) if id.len() == 32 => id,
        _ => {
            let id = new_install_id();
            let _ = set("InstallId", &id);
            id
        }
    };
    Store {
        highest: get("HighestCommitted").and_then(|v| Version::parse(&v)),
        seq: get("ManifestSeq").and_then(|v| v.parse().ok()).unwrap_or(0),
        revoked: json("RevokedKeys"),
        pin_add: json("PinAdd"),
        install_id: install_id.into_bytes(),
        pending: get("Pending").and_then(|t| serde_json::from_str(&t).ok()),
    }
}

/// The pins in force: compiled in, plus any a signed manifest added (adding only; removing one needs a new build).
pub fn pins(store: &Store) -> Vec<Pin> {
    let mut all = update::compiled_pins();
    if !cfg!(feature = "update-test") {
        all.extend(store.pin_add.iter().cloned());
    }
    all
}

// ----- HTTPS through WinHTTP ---------------------------------------------------------------------------------------

#[link(name = "winhttp")]
extern "system" {
    fn WinHttpOpen(agent: *const u16, access: u32, proxy: *const u16, bypass: *const u16, flags: u32) -> *mut c_void;
    fn WinHttpConnect(session: *mut c_void, server: *const u16, port: u16, reserved: u32) -> *mut c_void;
    fn WinHttpOpenRequest(connect: *mut c_void, verb: *const u16, object: *const u16, version: *const u16, referrer: *const u16, accept: *const *const u16, flags: u32) -> *mut c_void;
    fn WinHttpSetOption(handle: *mut c_void, option: u32, buffer: *const c_void, length: u32) -> i32;
    fn WinHttpSetTimeouts(handle: *mut c_void, resolve: i32, connect: i32, send: i32, receive: i32) -> i32;
    fn WinHttpSendRequest(request: *mut c_void, headers: *const u16, headers_length: u32, optional: *const c_void, optional_length: u32, total_length: u32, context: usize) -> i32;
    fn WinHttpReceiveResponse(request: *mut c_void, reserved: *mut c_void) -> i32;
    fn WinHttpQueryHeaders(request: *mut c_void, info_level: u32, name: *const u16, buffer: *mut c_void, length: *mut u32, index: *mut u32) -> i32;
    fn WinHttpReadData(request: *mut c_void, buffer: *mut c_void, to_read: u32, read: *mut u32) -> i32;
    fn WinHttpCloseHandle(handle: *mut c_void) -> i32;
}

const WINHTTP_ACCESS_TYPE_DEFAULT_PROXY: u32 = 0;
const WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY: u32 = 4;
const WINHTTP_FLAG_SECURE: u32 = 0x0080_0000;
const WINHTTP_FLAG_REFRESH: u32 = 0x0000_0100;
const WINHTTP_OPTION_SECURE_PROTOCOLS: u32 = 84;
const WINHTTP_OPTION_REDIRECT_POLICY: u32 = 88;
const WINHTTP_OPTION_REDIRECT_POLICY_NEVER: u32 = 0;
const WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2: u32 = 0x0000_0800;
const WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3: u32 = 0x0000_2000;
const WINHTTP_QUERY_STATUS_CODE: u32 = 19;
const WINHTTP_QUERY_FLAG_NUMBER: u32 = 0x2000_0000;

struct Http(*mut c_void);

impl Drop for Http {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { WinHttpCloseHandle(self.0) };
        }
    }
}

fn win_error(what: &str) -> String {
    format!("{what} (Windows error {})", std::io::Error::last_os_error().raw_os_error().unwrap_or(0))
}

/// GET `https://<host><path>`: TLS 1.2 or later, Windows' certificate checks, redirects refused (a 3xx is a failure), at most
/// `max` bytes passed to `sink` (one more is an error). Port 443 only.
pub fn https_get(host: &str, path: &str, max: u64, sink: &mut dyn FnMut(&[u8]) -> Result<(), String>) -> Result<(), String> {
    unsafe {
        let agent = wide(&format!("KiwiVPN-Service/{}", env!("CARGO_PKG_VERSION")));
        let mut session = Http(WinHttpOpen(agent.as_ptr(), WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, null(), null(), 0));
        if session.0.is_null() {
            session = Http(WinHttpOpen(agent.as_ptr(), WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, null(), null(), 0));
        }
        if session.0.is_null() {
            return Err(win_error("can't start an HTTPS session"));
        }
        let both = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 | WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3;
        if WinHttpSetOption(session.0, WINHTTP_OPTION_SECURE_PROTOCOLS, &both as *const u32 as *const c_void, 4) == 0 {
            let only = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2; // older Windows without TLS 1.3
            if WinHttpSetOption(session.0, WINHTTP_OPTION_SECURE_PROTOCOLS, &only as *const u32 as *const c_void, 4) == 0 {
                return Err(win_error("can't require TLS 1.2"));
            }
        }
        WinHttpSetTimeouts(session.0, 30_000, 30_000, 30_000, 60_000);
        let connect = Http(WinHttpConnect(session.0, wide(host).as_ptr(), 443, 0));
        if connect.0.is_null() {
            return Err(win_error("can't reach the update server"));
        }
        let request = Http(WinHttpOpenRequest(connect.0, wide("GET").as_ptr(), wide(path).as_ptr(), null(), null(), null(), WINHTTP_FLAG_SECURE | WINHTTP_FLAG_REFRESH));
        if request.0.is_null() {
            return Err(win_error("can't make the request"));
        }
        let never = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
        if WinHttpSetOption(request.0, WINHTTP_OPTION_REDIRECT_POLICY, &never as *const u32 as *const c_void, 4) == 0 {
            return Err(win_error("can't turn redirects off"));
        }
        if WinHttpSendRequest(request.0, null(), 0, null(), 0, 0, 0) == 0 || WinHttpReceiveResponse(request.0, null_mut()) == 0 {
            return Err(win_error("the update server didn't answer"));
        }
        let mut code = 0u32;
        let mut len = 4u32;
        if WinHttpQueryHeaders(request.0, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, null(), &mut code as *mut u32 as *mut c_void, &mut len, null_mut()) == 0 {
            return Err(win_error("no status from the update server"));
        }
        if code != 200 {
            return Err(format!("the update server answered HTTP {code}"));
        }
        let mut buf = vec![0u8; 65_536];
        let mut total = 0u64;
        loop {
            let mut got = 0u32;
            if WinHttpReadData(request.0, buf.as_mut_ptr() as *mut c_void, buf.len() as u32, &mut got) == 0 {
                return Err(win_error("the download broke off"));
            }
            if got == 0 {
                return Ok(());
            }
            total += u64::from(got);
            if total > max {
                return Err("the download is larger than allowed".into());
            }
            sink(&buf[..got as usize])?;
        }
    }
}

// ----- Authenticode, by handle ---------------------------------------------------------------------------------------

#[repr(C)]
struct WintrustFileInfo {
    cb_struct: u32,
    path: *const u16,
    file: HANDLE,
    known_subject: *const GUID,
}

#[repr(C)]
struct WintrustData {
    cb_struct: u32,
    policy_callback_data: *mut c_void,
    sip_client_data: *mut c_void,
    ui_choice: u32,
    revocation_checks: u32,
    union_choice: u32,
    file: *mut WintrustFileInfo,
    state_action: u32,
    state_data: HANDLE,
    url_reference: *mut u16,
    prov_flags: u32,
    ui_context: u32,
    signature_settings: *mut c_void,
}

#[repr(C)]
struct FileTime {
    low: u32,
    high: u32,
}

/// The start of CRYPT_PROVIDER_SGNR, as far as the fields read here.
#[repr(C)]
struct ProviderSigner {
    cb_struct: u32,
    verify_as_of: FileTime,
    cert_chain_count: u32,
    cert_chain: *const ProviderCert,
    signer_type: u32,
    signer: *mut c_void,
    error: u32,
    counter_signer_count: u32,
    counter_signers: *mut c_void,
    chain_context: *mut c_void,
}

/// The start of CRYPT_PROVIDER_CERT: only the first element of a chain is read, so the rest of the struct doesn't matter.
#[repr(C)]
struct ProviderCert {
    cb_struct: u32,
    cert: *const c_void,
}

#[link(name = "wintrust")]
extern "system" {
    fn WinVerifyTrust(hwnd: *mut c_void, action: *const GUID, data: *mut c_void) -> i32;
    fn WTHelperProvDataFromStateData(state: HANDLE) -> *mut c_void;
    fn WTHelperGetProvSignerFromChain(prov: *mut c_void, signer: u32, counter: i32, counter_index: u32) -> *mut ProviderSigner;
}

#[link(name = "crypt32")]
extern "system" {
    fn CertGetCertificateContextProperty(cert: *const c_void, prop: u32, data: *mut c_void, len: *mut u32) -> i32;
}

const WINTRUST_ACTION_GENERIC_VERIFY_V2: GUID = GUID::from_u128(0x00aac56b_cd44_11d0_8cc2_00c04fc295ee);
const WTD_UI_NONE: u32 = 2;
const WTD_REVOKE_NONE: u32 = 0;
const WTD_REVOKE_WHOLECHAIN: u32 = 1;
const WTD_CHOICE_FILE: u32 = 1;
const WTD_STATEACTION_VERIFY: u32 = 1;
const WTD_STATEACTION_CLOSE: u32 = 2;
const WTD_REVOCATION_CHECK_NONE: u32 = 0x10;
const WTD_REVOCATION_CHECK_CHAIN: u32 = 0x40;
const TRUST_E_NOSIGNATURE: i32 = 0x800B_0100_u32 as i32;
const CERT_SHA1_HASH_PROP_ID: u32 = 3;
const CERT_SHA256_HASH_PROP_ID: u32 = 107;

fn cert_hash(cert: *const c_void, prop: u32) -> String {
    let mut buf = [0u8; 64];
    let mut len = buf.len() as u32;
    if unsafe { CertGetCertificateContextProperty(cert, prop, buf.as_mut_ptr() as *mut c_void, &mut len) } == 0 {
        return String::new();
    }
    buf[..len as usize].iter().map(|b| format!("{b:02X}")).collect()
}

/// What Windows says about the signature of the file open at `file`. None: not signed at all. Whole-chain revocation checking
/// in a real build (revocation unknown = not trusted; try later); the test build's throwaway certificate has no revocation list.
pub fn signer_of(file: &File, path: &Path) -> Option<Signer> {
    let wpath = wide(&path.display().to_string());
    let mut info = WintrustFileInfo { cb_struct: size_of::<WintrustFileInfo>() as u32, path: wpath.as_ptr(), file: file.as_raw_handle() as HANDLE, known_subject: null() };
    let test = cfg!(feature = "update-test");
    let mut data = WintrustData {
        cb_struct: size_of::<WintrustData>() as u32,
        policy_callback_data: null_mut(),
        sip_client_data: null_mut(),
        ui_choice: WTD_UI_NONE,
        revocation_checks: if test { WTD_REVOKE_NONE } else { WTD_REVOKE_WHOLECHAIN },
        union_choice: WTD_CHOICE_FILE,
        file: &mut info,
        state_action: WTD_STATEACTION_VERIFY,
        state_data: null_mut(),
        url_reference: null_mut(),
        prov_flags: if test { WTD_REVOCATION_CHECK_NONE } else { WTD_REVOCATION_CHECK_CHAIN },
        ui_context: 0,
        signature_settings: null_mut(),
    };
    let action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    let result = unsafe { WinVerifyTrust(INVALID_HANDLE_VALUE, &action, &mut data as *mut WintrustData as *mut c_void) };
    let mut signer = None;
    if result != TRUST_E_NOSIGNATURE && !data.state_data.is_null() {
        unsafe {
            let prov = WTHelperProvDataFromStateData(data.state_data);
            let sgnr = if prov.is_null() { null_mut() } else { WTHelperGetProvSignerFromChain(prov, 0, 0, 0) };
            if !sgnr.is_null() && (*sgnr).cert_chain_count > 0 && !(*sgnr).cert_chain.is_null() {
                let cert = (*(*sgnr).cert_chain).cert;
                if !cert.is_null() {
                    signer = Some(Signer {
                        trusted: result == 0,
                        sha1: cert_hash(cert, CERT_SHA1_HASH_PROP_ID),
                        sha256: cert_hash(cert, CERT_SHA256_HASH_PROP_ID),
                        timestamped: (*sgnr).counter_signer_count > 0,
                    });
                }
            }
        }
    }
    data.state_action = WTD_STATEACTION_CLOSE;
    unsafe { WinVerifyTrust(INVALID_HANDLE_VALUE, &action, &mut data as *mut WintrustData as *mut c_void) };
    if signer.is_none() && result != TRUST_E_NOSIGNATURE {
        log_event(&format!("update: signature check said {result:#x} and no signer could be read"));
    }
    signer
}

// ----- the file's version resource -------------------------------------------------------------------------------------

#[link(name = "version")]
extern "system" {
    fn GetFileVersionInfoSizeW(path: *const u16, handle: *mut u32) -> u32;
    fn GetFileVersionInfoW(path: *const u16, handle: u32, len: u32, data: *mut c_void) -> i32;
    fn VerQueryValueW(block: *const c_void, sub: *const u16, buffer: *mut *mut c_void, len: *mut u32) -> i32;
}

#[repr(C)]
struct FixedFileInfo {
    signature: u32,
    struc_version: u32,
    file_version_ms: u32,
    file_version_ls: u32,
    product_version_ms: u32,
    product_version_ls: u32,
}

/// The product version an installer says it is (NSIS's VIProductVersion, e.g. 2.2.1.0), as major.minor.patch.
pub fn file_version(path: &Path) -> Option<Version> {
    let wpath = wide(&path.display().to_string());
    unsafe {
        let mut ignored = 0u32;
        let size = GetFileVersionInfoSizeW(wpath.as_ptr(), &mut ignored);
        if size == 0 || size > 1_048_576 {
            return None;
        }
        let mut data = vec![0u8; size as usize];
        if GetFileVersionInfoW(wpath.as_ptr(), 0, size, data.as_mut_ptr() as *mut c_void) == 0 {
            return None;
        }
        let mut ptr: *mut c_void = null_mut();
        let mut len = 0u32;
        if VerQueryValueW(data.as_ptr() as *const c_void, wide("\\").as_ptr(), &mut ptr, &mut len) == 0 || ptr.is_null() || (len as usize) < size_of::<FixedFileInfo>() {
            return None;
        }
        let f = &*(ptr as *const FixedFileInfo);
        if f.signature != 0xFEEF_04BD {
            return None;
        }
        Some(Version(f.product_version_ms >> 16, f.product_version_ms & 0xffff, f.product_version_ls >> 16))
    }
}

// ----- the updates folder ------------------------------------------------------------------------------------------------

fn install_dir() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    exe.parent().map(PathBuf::from).ok_or_else(|| "no install folder".to_string())
}

fn owner_of(path: &Path) -> Option<String> {
    unsafe {
        let mut owner: *mut c_void = null_mut();
        let mut sd: PSECURITY_DESCRIPTOR = null_mut();
        let got = GetNamedSecurityInfoW(wide(&path.display().to_string()).as_ptr(), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION, &mut owner as *mut _ as _, null_mut(), null_mut(), null_mut(), &mut sd);
        if got != 0 {
            return None;
        }
        let mut text: *mut u16 = null_mut();
        let sid = if ConvertSidToStringSidW(owner as _, &mut text) == 0 {
            None
        } else {
            let len = (0..).take_while(|&i| *text.add(i) != 0).count();
            let s = String::from_utf16_lossy(std::slice::from_raw_parts(text, len));
            LocalFree(text as _);
            Some(s)
        };
        LocalFree(sd as _);
        sid
    }
}

/// `<install folder>\updates`, made by the service with SYSTEM and Administrators only. One owned by anyone else is replaced.
pub fn updates_dir() -> Result<PathBuf, String> {
    let dir = install_dir()?.join("updates");
    if dir.exists() && !matches!(owner_of(&dir).as_deref(), Some("S-1-5-18" | "S-1-5-32-544")) {
        log_event("update: the updates folder had an unexpected owner, replacing it");
        std::fs::remove_dir_all(&dir).map_err(|e| format!("can't replace the updates folder: {e}"))?;
    }
    let sd = Descriptor::new(PRIVATE_SDDL)?;
    let attrs = sd.attributes();
    unsafe { CreateDirectoryW(wide(&dir.display().to_string()).as_ptr(), &attrs) };
    if !dir.is_dir() {
        return Err("can't create the updates folder".into());
    }
    Ok(dir)
}

fn installer_name(version: Version) -> String {
    format!("KiwiVPN-{version}-Setup.exe")
}

// ----- recovery tasks ----------------------------------------------------------------------------------------------------

const RECOVERY_TASKS: [&str; 2] = ["KiwiVPN Update Recovery", "KiwiVPN Update Recovery at boot"];

fn schtasks() -> std::process::Command {
    let mut c = std::process::Command::new(std::env::var("SystemRoot").map(|r| format!(r"{r}\System32\schtasks.exe")).unwrap_or_else(|_| "schtasks.exe".into()));
    c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    c
}

/// Every 10 minutes and at boot, as SYSTEM, until the new version commits: `kiwivpn-service recover`.
fn create_recovery_tasks() -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let action = format!("\"\\\"{}\\\" recover\"", exe.display());
    for (name, schedule) in RECOVERY_TASKS.iter().zip([["/SC", "MINUTE", "/MO", "10"].as_slice(), ["/SC", "ONSTART"].as_slice()]) {
        let ok = schtasks().args(["/Create", "/F", "/RU", "SYSTEM", "/RL", "HIGHEST", "/TN", *name]).args(schedule).arg("/TR").raw_arg(&action).status().map(|s| s.success()).unwrap_or(false);
        if !ok {
            return Err(format!("can't create the recovery task {name}"));
        }
    }
    Ok(())
}

pub fn delete_recovery_tasks() {
    for name in RECOVERY_TASKS {
        let _ = schtasks().args(["/Delete", "/F", "/TN", name]).status();
    }
}

// ----- the updater -----------------------------------------------------------------------------------------------------

/// Ask Windows again after a failed install of the same file at most this often (1 h, 6 h, 24 h, then stop).
const BACKOFF: [u64; 3] = [3600, 6 * 3600, 24 * 3600];

pub struct Updater {
    info: Mutex<UpdateInfo>,
    offer: Mutex<Option<(Version, Release)>>,
    last_check: Mutex<Option<Instant>>,
    busy: AtomicBool,
    failures: Mutex<(String, u32, u64)>,
}

impl Default for Updater {
    fn default() -> Self {
        Self::new()
    }
}

impl Updater {
    pub fn new() -> Updater {
        let info = if update::keys_configured(update::trusted_keys()) {
            UpdateInfo::default()
        } else {
            UpdateInfo { error: Some("Automatic updates aren't set up in this build yet.".into()), ..UpdateInfo::default() }
        };
        Updater { info: Mutex::new(info), offer: Mutex::new(None), last_check: Mutex::new(None), busy: AtomicBool::new(false), failures: Mutex::new((String::new(), 0, 0)) }
    }

    pub fn info(&self) -> UpdateInfo {
        self.info.lock().unwrap().clone()
    }

    fn set_state(&self, state: UpdateState, error: Option<String>) {
        let mut i = self.info.lock().unwrap();
        i.state = state;
        i.error = error;
    }

    /// `by_app`: limited to once per UPDATE_CHECK_EVERY_SECONDS. Returns what is now known.
    pub fn check(&self, by_app: bool) -> UpdateInfo {
        if !update::keys_configured(update::trusted_keys()) || self.busy.load(Ordering::SeqCst) {
            return self.info();
        }
        {
            let mut last = self.last_check.lock().unwrap();
            // the CI update test asks many times in a row; a real build limits the app's asking
            if by_app && !cfg!(feature = "update-test") && last.is_some_and(|t| t.elapsed() < Duration::from_secs(crate::protocol::UPDATE_CHECK_EVERY_SECONDS)) {
                return self.info();
            }
            *last = Some(Instant::now());
        }
        let mut bytes = Vec::new();
        let fetched = https_get(update::host(), &update::manifest_path(update::channel()), update::MAX_MANIFEST as u64, &mut |chunk| {
            bytes.extend_from_slice(chunk);
            Ok(())
        });
        if let Err(e) = fetched {
            log_event(&format!("update: couldn't fetch the manifest: {e}"));
            self.info.lock().unwrap().error = Some("Couldn't check for updates.".into());
            return self.info();
        }
        self.judge(&bytes)
    }

    /// The manifest's verdict, stored. Separate from the fetch so the rules are the same whatever brought the bytes.
    fn judge(&self, bytes: &[u8]) -> UpdateInfo {
        let store = load();
        let running = update::running_version();
        let cx = update::Context { now: now(), running, highest: store.highest, seq: store.seq, product: update::PRODUCT, channel: update::channel(), host: update::host(), install_id: &store.install_id };
        match update::accept(bytes, update::trusted_keys(), &store.revoked, &cx) {
            Ok(a) => {
                if a.manifest.seq > store.seq {
                    let _ = set("ManifestSeq", &a.manifest.seq.to_string());
                    let _ = set("ManifestSignedAt", &a.manifest.signed_at);
                }
                if !a.revoke.is_empty() {
                    let mut revoked = store.revoked.clone();
                    revoked.extend(a.revoke.iter().filter(|k| !store.revoked.contains(k)).cloned());
                    let _ = set("RevokedKeys", &serde_json::to_string(&revoked).unwrap_or_default());
                    log_event(&format!("update: manifest revoked keys {:?}", a.revoke));
                }
                if !a.manifest.pin_add.is_empty() {
                    let mut added = store.pin_add.clone();
                    added.extend(a.manifest.pin_add.iter().filter(|p| !store.pin_add.contains(p)).cloned());
                    let _ = set("PinAdd", &serde_json::to_string(&added).unwrap_or_default());
                }
                let mut info = self.info.lock().unwrap();
                info.checked_at = Some(now());
                info.error = None;
                match a.offer {
                    Offer::Available { version, required, release } => {
                        log_event(&format!("update: {version} available (required {required}, security {})", release.security));
                        info.state = if required { UpdateState::Required } else { UpdateState::Available };
                        info.version = Some(version.to_string());
                        info.notes = Some(release.notes.clone());
                        info.security = release.security;
                        *self.offer.lock().unwrap() = Some((version, release));
                    }
                    Offer::Nothing(why) => {
                        log_event(&format!("update: nothing to install ({why})"));
                        *info = UpdateInfo { state: UpdateState::None, checked_at: info.checked_at, ..UpdateInfo::default() };
                        *self.offer.lock().unwrap() = None;
                    }
                }
                info.clone()
            }
            Err(refused) => {
                log_event(&format!("update: manifest refused: {refused}"));
                self.info.lock().unwrap().error = Some(format!("The update information wasn't valid ({refused})."));
                self.info()
            }
        }
    }

    /// Whether an update may install without a click: a security fix (or a required one) while no tunnel is wanted.
    pub fn wants_silent_install(&self) -> bool {
        self.offer.lock().unwrap().as_ref().is_some_and(|(_, r)| r.security) || self.info().state == UpdateState::Required
    }

    /// Downloads, verifies and starts the installer. `relaunch`: the person clicked Install, so the installer reopens the app.
    pub fn install(&self, relaunch: bool) -> Result<(), String> {
        let Some((version, release)) = self.offer.lock().unwrap().clone() else { return Err("There's no update to install.".into()) };
        if self.busy.swap(true, Ordering::SeqCst) {
            return Ok(());
        }
        let result = self.install_inner(version, &release, relaunch);
        if let Err(e) = &result {
            log_event(&format!("update: install of {version} stopped: {e}"));
            let mut f = self.failures.lock().unwrap();
            if f.0 != release.sha256 {
                *f = (release.sha256.clone(), 0, 0);
            }
            f.1 += 1;
            f.2 = now();
            self.set_state(UpdateState::Failed, Some(if f.1 >= 3 { "The update failed. Please download Kiwi VPN again from pricechat.live.".into() } else { format!("The update couldn't be installed ({e}). Kiwi VPN will try again later.") }));
            self.busy.store(false, Ordering::SeqCst);
        }
        result
    }

    /// True when a failed file may be tried again now (backoff), false after three failures of the same file.
    pub fn may_retry(&self) -> bool {
        let f = self.failures.lock().unwrap();
        f.1 == 0 || (f.1 < 3 && now().saturating_sub(f.2) >= BACKOFF[f.1 as usize - 1])
    }

    fn install_inner(&self, version: Version, release: &Release, relaunch: bool) -> Result<(), String> {
        let store = load();
        update::may_run(version, store.highest, false).map_err(|r| r.to_string())?;
        self.set_state(UpdateState::Downloading, None);
        let dir = updates_dir()?;
        let part = dir.join("download.part");
        let _ = std::fs::remove_file(&part);
        let mut out = std::fs::OpenOptions::new().write(true).create_new(true).open(&part).map_err(|e| format!("can't save the download: {e}"))?;
        let mut check = Download::new(release.size, &release.sha256).map_err(|r| r.to_string())?;
        let path = update::url_path(&release.url, update::host()).to_string();
        let fetched = https_get(update::host(), &path, release.size, &mut |chunk| {
            check.feed(chunk).map_err(|r| r.to_string())?;
            out.write_all(chunk).map_err(|e| e.to_string())
        });
        drop(out);
        if let Err(e) = fetched.and_then(|_| check.finish().map_err(|r| r.to_string())) {
            let _ = std::fs::remove_file(&part);
            return Err(e);
        }
        let file_path = dir.join(installer_name(version));
        let _ = std::fs::remove_file(&file_path);
        std::fs::rename(&part, &file_path).map_err(|e| format!("can't keep the download: {e}"))?;
        // From here the file is open with read sharing only: nobody can change or delete it until the installer has started.
        let file = std::fs::OpenOptions::new().read(true).share_mode(FILE_SHARE_READ).open(&file_path).map_err(|e| format!("can't open the download: {e}"))?;
        if let Err(r) = verify_open(&file, &file_path, version, release.size, &release.sha256, &pins(&store)) {
            drop(file);
            let _ = std::fs::remove_file(&file_path);
            return Err(r.to_string());
        }
        let pending = Pending { version: version.to_string(), sha256: release.sha256.clone(), started_at: now(), attempts: 0 };
        set("Pending", &serde_json::to_string(&pending).unwrap_or_default())?;
        if let Err(e) = create_recovery_tasks() {
            clear("Pending");
            return Err(e);
        }
        self.set_state(UpdateState::Installing, None);
        log_event(&format!("update: {version} verified, starting its installer"));
        if let Err(e) = run_installer(&file_path, relaunch) {
            clear("Pending");
            delete_recovery_tasks();
            return Err(e);
        }
        drop(file); // the installer is running; it stops this service next
        Ok(())
    }
}

/// Every check on the open file: size and SHA-256 again (read through the same handle), the Authenticode signature by handle,
/// the pinned certificate, the timestamp, and the version the file says it is.
pub fn verify_open(file: &File, path: &Path, version: Version, size: u64, sha256: &str, pins: &[Pin]) -> Result<(), Refused> {
    let mut check = Download::new(size, sha256)?;
    let mut f = file;
    f.seek(SeekFrom::Start(0)).map_err(|_| Refused::Truncated)?;
    let mut buf = vec![0u8; 65_536];
    loop {
        let n = f.read(&mut buf).map_err(|_| Refused::Truncated)?;
        if n == 0 {
            break;
        }
        check.feed(&buf[..n])?;
    }
    check.finish()?;
    update::installer_ok(signer_of(file, path).as_ref(), pins, file_version(path), version)
}

/// As SYSTEM, silently, detached: it outlives this service, which the installer stops. `/R` asks the installer to reopen the app
/// in the signed-in person's session (best effort; the installer itself never runs the app as SYSTEM).
fn run_installer(path: &Path, relaunch: bool) -> Result<(), String> {
    const DETACHED_PROCESS: u32 = 0x0000_0008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
    let mut args = vec!["/S"];
    if relaunch {
        args.push("/R");
    }
    let spawn = |flags: u32| std::process::Command::new(path).args(&args).creation_flags(flags).spawn();
    match spawn(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB) {
        Ok(_) => Ok(()),
        // not in a job, or a job that doesn't allow breaking away: start it plainly
        Err(_) => spawn(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP).map(|_| ()).map_err(|e| format!("can't start the installer: {e}")),
    }
}

/// At service start: an update in flight either committed (this is the new version) or didn't.
pub fn after_start(updater: &Updater) {
    let store = load();
    let running = update::running_version();
    match update::after_start(store.pending.as_ref(), running) {
        Health::Idle => {}
        Health::Commit => {
            let _ = set("HighestCommitted", &running.to_string());
            clear("Pending");
            delete_recovery_tasks();
            keep_repair_copy(running);
            log_event(&format!("update: {running} installed and running, committed"));
            let mut i = updater.info.lock().unwrap();
            *i = UpdateInfo { state: UpdateState::None, checked_at: i.checked_at, ..UpdateInfo::default() };
        }
        Health::Recover => {
            // the old version is running again: the installer didn't finish. Nothing to roll back; try again later.
            let version = store.pending.as_ref().map(|p| p.version.clone()).unwrap_or_default();
            log_event(&format!("update: {version} didn't finish installing, {running} is still running"));
            clear("Pending");
            delete_recovery_tasks();
            updater.set_state(UpdateState::Failed, Some("The last update didn't finish. Kiwi VPN will try again later.".into()));
        }
    }
    if store.highest.is_none_or(|h| running > h) && store.pending.is_none() {
        // a version installed by hand counts as committed: it's the floor from now on
        let _ = set("HighestCommitted", &running.to_string());
    }
}

/// The installer of the committed version stays as the repair copy; older ones go.
fn keep_repair_copy(version: Version) {
    let Ok(dir) = updates_dir() else { return };
    let keep = installer_name(version);
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for e in entries.flatten() {
            if e.file_name().to_string_lossy() != keep {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
}

/// `kiwivpn-service recover`, run by the recovery task while an update is in flight. Moves forward or reinstalls the committed
/// version; never installs anything lower than HighestCommitted. Returns a line for the console.
pub fn recover(service_version: Option<Version>) -> String {
    let store = load();
    let Some(mut pending) = store.pending.clone() else {
        delete_recovery_tasks();
        return "No update in flight.".into();
    };
    let Some(target) = Version::parse(&pending.version) else {
        clear("Pending");
        delete_recovery_tasks();
        return "The pending update was unreadable; cleared.".into();
    };
    if service_version == Some(target) {
        return "The new version is running; it commits itself.".into();
    }
    if now().saturating_sub(pending.started_at) < 120 {
        return "The update is still installing.".into();
    }
    let Ok(dir) = updates_dir() else { return "No updates folder.".into() };
    let pins = pins(&store);
    let attempt = |version: Version, sha: Option<&str>, repair: bool| -> Result<(), String> {
        update::may_run(version, store.highest, repair).map_err(|r| r.to_string())?;
        let path = dir.join(installer_name(version));
        let file = std::fs::OpenOptions::new().read(true).share_mode(FILE_SHARE_READ).open(&path).map_err(|e| e.to_string())?;
        let size = file.metadata().map_err(|e| e.to_string())?.len();
        let sha = match sha {
            Some(s) => s.to_string(),
            None => update::sha256_hex(&std::fs::read(&path).map_err(|e| e.to_string())?),
        };
        verify_open(&file, &path, version, size, &sha, &pins).map_err(|r| r.to_string())?;
        let status = std::process::Command::new(&path).arg("/S").status().map_err(|e| e.to_string())?;
        drop(file);
        if status.success() {
            Ok(())
        } else {
            Err(format!("the installer exited with {status}"))
        }
    };
    if pending.attempts == 0 {
        pending.attempts = 1;
        let _ = set("Pending", &serde_json::to_string(&pending).unwrap_or_default());
        log_event(&format!("update: recovery, running the {target} installer again"));
        match attempt(target, Some(&pending.sha256), false) {
            Ok(()) => return format!("Ran the {target} installer again."),
            Err(e) => log_event(&format!("update: recovery, {target} failed again: {e}")),
        }
    }
    clear("Pending");
    delete_recovery_tasks();
    let Some(highest) = store.highest else { return "No committed version to repair with.".into() };
    log_event(&format!("update: recovery, reinstalling the committed {highest}"));
    match attempt(highest, None, true) {
        Ok(()) => format!("Reinstalled {highest}."),
        Err(e) => {
            log_event(&format!("update: recovery, the repair copy failed: {e}"));
            format!("Recovery failed: {e}")
        }
    }
}

/// For the CI update test (the test build only): judge a manifest file exactly as a downloaded one would be.
#[doc(hidden)]
pub fn judge_file_for_tests(updater: &Updater, bytes: &[u8]) -> UpdateInfo {
    updater.judge(bytes)
}
