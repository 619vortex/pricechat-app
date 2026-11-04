//! A stand-in attacker, for the security test only: never shipped. It tries the two ways a program without administrator rights could pose as the Kiwi VPN service, and
//! reports exactly what it was sent, so the test can prove no connect request reached it. It keeps nothing.
//!   fake_service instance        add an instance to the real service's pipe; passes if Windows refuses
//!   fake_service serve <seconds> take the pipe's name while the service is stopped, and listen
//!   fake_service control         send a harmless line without checking who's listening, to prove the listener works

#[cfg(windows)]
pub mod fake {
    use std::ptr::{null, null_mut};
    use std::sync::{Arc, Mutex};
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::Pipes::*;

    pub const PIPE_NAME: &str = r"\\.\pipe\KiwiVPN";

    pub fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// `first` claims the name, as a squatter would while the service is stopped. Without it, this asks to join a pipe that already exists.
    pub fn create(first: bool) -> Result<HANDLE, u32> {
        let flags = PIPE_ACCESS_DUPLEX | if first { FILE_FLAG_FIRST_PIPE_INSTANCE } else { 0 };
        let pipe = unsafe { CreateNamedPipeW(wide(PIPE_NAME).as_ptr(), flags, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT, PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, null()) };
        if pipe == INVALID_HANDLE_VALUE {
            return Err(unsafe { GetLastError() });
        }
        Ok(pipe)
    }

    /// Takes one client on this instance and returns everything it sent up to a line's end or hanging up.
    pub fn receive(pipe: HANDLE) -> Vec<u8> {
        let mut data = Vec::new();
        unsafe {
            if ConnectNamedPipe(pipe, null_mut()) == 0 && GetLastError() != ERROR_PIPE_CONNECTED {
                CloseHandle(pipe);
                return data;
            }
            let mut chunk = [0u8; 4096];
            loop {
                let mut got = 0u32;
                if ReadFile(pipe, chunk.as_mut_ptr(), chunk.len() as u32, &mut got, null_mut()) == 0 || got == 0 {
                    break;
                }
                data.extend_from_slice(&chunk[..got as usize]);
                if data.last() == Some(&b'\n') {
                    break;
                }
            }
            CloseHandle(pipe);
        }
        data
    }

    /// Takes every client until the process ends, keeping a verdict on each rather than what it said.
    pub fn listen(log: Arc<Mutex<Vec<String>>>) {
        let mut first = true;
        loop {
            let pipe = match create(first) {
                Ok(p) => p,
                Err(e) => {
                    println!("CANT_LISTEN {e}");
                    return;
                }
            };
            if first {
                println!("LISTENING");
            }
            first = false;
            let raw = pipe as usize;
            let log = Arc::clone(&log);
            std::thread::spawn(move || {
                let data = receive(raw as HANDLE);
                let text = String::from_utf8_lossy(&data);
                let verdict = if data.is_empty() { "nothing" } else if text.contains("control-line") { "control" } else { "CAUGHT" };
                println!("client sent {} bytes: {verdict}", data.len());
                log.lock().unwrap().push(verdict.to_string());
            });
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
    }
}

#[cfg(windows)]
fn main() {
    use std::io::Write;
    use std::sync::{Arc, Mutex};
    use std::time::Duration;
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["instance"] => match fake::create(false) {
            Ok(_) => {
                println!("CREATED: an instance was added to the service's pipe");
                std::process::exit(1);
            }
            Err(e) => println!("REFUSED {e}"),
        },
        ["serve", seconds] => {
            let log = Arc::new(Mutex::new(Vec::new()));
            let worker = Arc::clone(&log);
            std::thread::spawn(move || fake::listen(worker));
            std::thread::sleep(Duration::from_secs(seconds.parse().expect("seconds")));
            println!("DONE {:?}", log.lock().unwrap());
            std::process::exit(0);
        }
        ["control"] => {
            let mut pipe = kiwivpn::windows::open_pipe().expect("nothing is listening");
            pipe.write_all(b"{\"cmd\":\"control-line\"}\n").expect("write");
            println!("SENT");
        }
        _ => {
            eprintln!("usage: fake_service instance | serve <seconds> | control");
            std::process::exit(2);
        }
    }
}

#[cfg(not(windows))]
fn main() {}
