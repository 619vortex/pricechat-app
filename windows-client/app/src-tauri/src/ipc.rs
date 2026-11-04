//! The app's end of the pipe to the Kiwi VPN service. The protocol is the service's own, so the two can't drift apart.
//!
//! The extra step here is checking who owns the pipe before anything is sent. A connect request carries the device's private key, and while the service is stopped any
//! program could create a pipe with this name and wait for it. Only an administrator's process can own a pipe as SYSTEM or Administrators, so a program posing as the
//! service is refused. (The service's own `ask` skips this, since it's used by tests that run the loop without administrator rights.)

use kiwivpn::protocol::{Request, Status};
use std::io::{BufRead, BufReader, Write};

#[derive(Debug, PartialEq)]
pub enum IpcError {
    /// The pipe isn't there: the service isn't installed or isn't running.
    NoService,
    /// Something other than the service owns the pipe. Nothing was sent to it.
    NotTheService,
    Broken(String),
}

impl IpcError {
    pub fn message(&self) -> String {
        match self {
            IpcError::NoService => "The Kiwi VPN service isn't running.".into(),
            IpcError::NotTheService => "Something other than the Kiwi VPN service is answering. Nothing was sent to it.".into(),
            IpcError::Broken(e) => format!("Couldn't talk to the Kiwi VPN service: {e}"),
        }
    }
}

/// SYSTEM, or the Administrators group, which is who Windows may record as the owner of what a SYSTEM service creates.
pub fn trusted_owner(sid: &str) -> bool {
    sid == "S-1-5-18" || sid == "S-1-5-32-544"
}

#[cfg(windows)]
fn pipe_owner(pipe: &std::fs::File) -> Result<String, String> {
    use std::os::windows::io::AsRawHandle;
    use std::ptr::null_mut;
    use windows_sys::Win32::Foundation::{GetLastError, LocalFree, ERROR_SUCCESS};
    use windows_sys::Win32::Security::Authorization::{ConvertSidToStringSidW, GetSecurityInfo, SE_KERNEL_OBJECT};
    use windows_sys::Win32::Security::{OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID};
    unsafe {
        let mut owner: PSID = null_mut();
        let mut sd: PSECURITY_DESCRIPTOR = null_mut();
        let got = GetSecurityInfo(pipe.as_raw_handle() as _, SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION, &mut owner, null_mut(), null_mut(), null_mut(), &mut sd);
        if got != ERROR_SUCCESS {
            return Err(format!("Windows error {got}"));
        }
        let mut text: *mut u16 = null_mut();
        let ok = ConvertSidToStringSidW(owner, &mut text);
        let result = if ok == 0 {
            Err(format!("Windows error {}", GetLastError()))
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

/// One request, one answer, over a fresh connection. Blocks: call it off the UI thread.
#[cfg(windows)]
pub fn ask(request: &Request) -> Result<Status, IpcError> {
    let mut pipe = kiwivpn::windows::open_pipe().map_err(|_| IpcError::NoService)?;
    let owner = pipe_owner(&pipe).map_err(IpcError::Broken)?;
    if !trusted_owner(&owner) {
        return Err(IpcError::NotTheService);
    }
    let mut line = serde_json::to_vec(request).map_err(|e| IpcError::Broken(e.to_string()))?;
    line.push(b'\n');
    pipe.write_all(&line).map_err(|e| IpcError::Broken(e.to_string()))?;
    let mut text = String::new();
    BufReader::new(pipe).read_line(&mut text).map_err(|e| IpcError::Broken(e.to_string()))?;
    serde_json::from_str(text.trim()).map_err(|_| IpcError::Broken("it gave an answer I can't read".into()))
}

#[cfg(not(windows))]
pub fn ask(_request: &Request) -> Result<Status, IpcError> {
    Err(IpcError::NoService)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_system_or_administrators_count_as_the_service() {
        assert!(trusted_owner("S-1-5-18"));
        assert!(trusted_owner("S-1-5-32-544"));
        for other in ["S-1-5-11", "S-1-5-21-1-2-3-1001", "S-1-1-0", ""] {
            assert!(!trusted_owner(other), "{other}");
        }
    }
}
