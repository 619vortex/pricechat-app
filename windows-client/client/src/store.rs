//! What the app remembers on this PC, under the person's own AppData. The session and each private key are encrypted with Windows DPAPI: another Windows user, or someone with
//! only the disk, can't read them. A program running as this same user can, which is why the session only reaches the VPN, and why `docs/LOCAL-SECURITY.md` plans a signed server list.

use crate::flow::{Saved, Vault};
use std::path::PathBuf;
use std::ptr::{null, null_mut};
use windows_sys::Win32::Foundation::LocalFree;
use windows_sys::Win32::Security::Cryptography::{CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB};
use zeroize::Zeroizing;

/// Mixed into the encryption, so this data only opens for this app's purpose.
const ENTROPY: &[u8] = b"live.pricechat.kiwivpn.v1";

fn dpapi(data: &[u8], protect: bool) -> Result<Zeroizing<Vec<u8>>, String> {
    let input = CRYPT_INTEGER_BLOB { cbData: data.len() as u32, pbData: data.as_ptr() as *mut u8 };
    let entropy = CRYPT_INTEGER_BLOB { cbData: ENTROPY.len() as u32, pbData: ENTROPY.as_ptr() as *mut u8 };
    let mut output = CRYPT_INTEGER_BLOB { cbData: 0, pbData: null_mut() };
    let ok = unsafe {
        if protect {
            CryptProtectData(&input, null(), &entropy, null(), null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output)
        } else {
            CryptUnprotectData(&input, null_mut(), &entropy, null(), null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output)
        }
    };
    if ok == 0 {
        return Err("Windows couldn't open the saved data.".into());
    }
    let out = unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
    unsafe {
        std::ptr::write_bytes(output.pbData, 0, output.cbData as usize);
        LocalFree(output.pbData as _);
    }
    Ok(Zeroizing::new(out))
}

pub struct FileVault {
    dir: PathBuf,
}

impl FileVault {
    pub fn open() -> Result<Self, String> {
        let base = std::env::var_os("LOCALAPPDATA").ok_or("Windows didn't say where app data goes.")?;
        Self::at(PathBuf::from(base).join("live.pricechat.kiwivpn").join("account"))
    }

    pub fn at(dir: PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(&dir).map_err(|e| format!("Couldn't create the app's folder: {e}"))?;
        Ok(FileVault { dir })
    }

    fn write_secret(&self, name: &str, text: &str) -> Result<(), String> {
        std::fs::write(self.dir.join(name), &*dpapi(text.as_bytes(), true)?).map_err(|e| format!("Couldn't save: {e}"))
    }

    fn read_secret(&self, name: &str) -> Option<Zeroizing<String>> {
        let raw = std::fs::read(self.dir.join(name)).ok()?;
        let open = dpapi(&raw, false).ok()?;
        String::from_utf8(open.to_vec()).ok().map(Zeroizing::new)
    }

    fn key_name(server_id: i64) -> String {
        format!("device-{server_id}.key")
    }
}

impl Vault for FileVault {
    fn session(&self) -> Option<Zeroizing<String>> {
        self.read_secret("session.bin")
    }

    fn save_session(&self, token: &str) -> Result<(), String> {
        self.write_secret("session.bin", token)
    }

    fn device(&self, server_id: i64) -> Option<Saved> {
        let id: i64 = std::fs::read_to_string(self.dir.join(format!("device-{server_id}.id"))).ok()?.trim().parse().ok()?;
        Some(Saved { device_id: id, private_b64: self.read_secret(&Self::key_name(server_id))? })
    }

    fn save_device(&self, server_id: i64, device_id: i64, private_b64: &str) -> Result<(), String> {
        self.write_secret(&Self::key_name(server_id), private_b64)?;
        std::fs::write(self.dir.join(format!("device-{server_id}.id")), device_id.to_string()).map_err(|e| format!("Couldn't save: {e}"))
    }

    fn forget_device(&self, server_id: i64) {
        let _ = std::fs::remove_file(self.dir.join(Self::key_name(server_id)));
        let _ = std::fs::remove_file(self.dir.join(format!("device-{server_id}.id")));
    }

    fn last_server(&self) -> Option<i64> {
        std::fs::read_to_string(self.dir.join("server.txt")).ok()?.trim().parse().ok()
    }

    fn save_last_server(&self, server_id: i64) {
        let _ = std::fs::write(self.dir.join("server.txt"), server_id.to_string());
    }

    fn forget_all(&self) {
        let _ = std::fs::remove_dir_all(&self.dir);
        let _ = std::fs::create_dir_all(&self.dir);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> FileVault {
        let mut raw = [0u8; 8];
        getrandom::getrandom(&mut raw).unwrap();
        FileVault::at(std::env::temp_dir().join(format!("kiwi-vault-{raw:02x?}").replace([' ', ',', '[', ']'], ""))).unwrap()
    }

    #[test]
    fn secrets_come_back_and_are_never_plain_on_disk() {
        let v = temp();
        v.save_session("kd.secret-session-token").unwrap();
        v.save_device(2, 77, "PRIVATE-KEY-TEXT").unwrap();
        assert_eq!(v.session().unwrap().as_str(), "kd.secret-session-token");
        let saved = v.device(2).unwrap();
        assert_eq!((saved.device_id, saved.private_b64.as_str()), (77, "PRIVATE-KEY-TEXT"));
        for file in std::fs::read_dir(&v.dir).unwrap() {
            let raw = std::fs::read(file.unwrap().path()).unwrap();
            let text = String::from_utf8_lossy(&raw);
            assert!(!text.contains("secret-session") && !text.contains("PRIVATE-KEY"));
        }
        v.forget_all();
        assert!(v.session().is_none() && v.device(2).is_none());
        let _ = std::fs::remove_dir_all(&v.dir);
    }

    #[test]
    fn a_tampered_file_opens_as_nothing() {
        let v = temp();
        v.save_session("token").unwrap();
        let path = v.dir.join("session.bin");
        let mut raw = std::fs::read(&path).unwrap();
        let last = raw.len() - 1;
        raw[last] ^= 0xff;
        std::fs::write(&path, raw).unwrap();
        assert!(v.session().is_none());
        let _ = std::fs::remove_dir_all(&v.dir);
    }
}
