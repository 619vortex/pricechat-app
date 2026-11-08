//! A fake service on the real pipe name: the client must refuse it before sending anything. No service, tunnel or real key is involved: the "key" below is made up.
//! It needs the name free, so it fails if the real service is running.
#![cfg(windows)]

#[path = "../examples/fake_service.rs"]
#[allow(dead_code)]
mod attacker;

use attacker::fake;
use kiwivpn::protocol::Request;
use kiwivpn::windows::{ask_service, ask_unverified, open_pipe, pipe_under_real_rule_for_tests, AskError};

const CANARY: &str = "[Interface]\nPrivateKey = S0lXSUNBTkFSWUtJV0lDQU5BUllLSVdJQ0FOQVJZIT0=\n";

fn listen_once() -> std::thread::JoinHandle<Vec<u8>> {
    let raw = fake::create(true).expect("the pipe's name is taken: stop the Kiwi VPN service first") as usize;
    std::thread::spawn(move || fake::receive(raw as _))
}

/// The service's own pipe rule: this program holds an instance, and still can't add one, which is exactly what a program posing as the service would need to do.
fn the_real_rule_lets_people_use_the_pipe_but_not_join_it() {
    let pipe = pipe_under_real_rule_for_tests().expect("the pipe's name is taken: stop the Kiwi VPN service first");
    assert_eq!(fake::create(false).unwrap_err(), 5, "adding an instance must be refused with access denied");
    let client = open_pipe().expect("the signed-in person must be able to use the pipe");
    drop(client);
    let generic = std::fs::OpenOptions::new().read(true).write(true).open(fake::PIPE_NAME);
    assert_eq!(generic.unwrap_err().raw_os_error(), Some(5), "generic write includes creating instances, so the rule refuses it");
    unsafe { windows_sys::Win32::Foundation::CloseHandle(pipe) };
}

#[test]
fn a_fake_service_gets_nowhere() {
    the_real_rule_lets_people_use_the_pipe_but_not_join_it();
    a_fake_service_never_receives_the_key();
}

fn a_fake_service_never_receives_the_key() {
    // the control: without the check, the fake really would catch it, so a pass below means the check did the work
    let fake_pipe = listen_once();
    let _ = ask_unverified(&Request::connect(CANARY));
    let caught = fake_pipe.join().unwrap();
    assert!(String::from_utf8_lossy(&caught).contains("PrivateKey"), "the fake should have caught the unchecked request");

    let fake_pipe = listen_once();
    let refused = ask_service(&Request::connect(CANARY));
    assert_eq!(refused.unwrap_err(), AskError::NotTheService);
    assert!(fake_pipe.join().unwrap().is_empty(), "the checked request must send nothing");

    let fake_pipe = listen_once();
    assert_eq!(ask_service(&Request::Status).unwrap_err(), AskError::NotTheService);
    assert!(fake_pipe.join().unwrap().is_empty());

    assert_eq!(ask_service(&Request::Status).unwrap_err(), AskError::NoService, "and with nothing on the pipe, it says the service isn't running");
}
