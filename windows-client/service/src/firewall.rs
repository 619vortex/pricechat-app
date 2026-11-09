//! `guard`'s rules as Windows Filtering Platform filters, in a dynamic session: Windows deletes every one of them when the session's
//! handle closes or the process holding it ends, crashed or not. Nothing here is ever persistent, so nothing here can outlive Kiwi.

use crate::guard::{rules, Cond, Dir, Plan, Rule};
use std::ffi::c_void;
use std::mem::zeroed;
use std::path::Path;
use std::ptr::{null, null_mut};
use windows_sys::core::GUID;
use windows_sys::Win32::Foundation::HANDLE;
use windows_sys::Win32::NetworkManagement::WindowsFilteringPlatform::*;

/// A fresh sublayer for each set of rules, so a new set can go in while the one it replaces still holds (one fixed key made the
/// second set fail with FWP_E_ALREADY_EXISTS). Its filters are judged on their own, and a block here holds whatever any other
/// sublayer permits. Dynamic, like everything here: gone with the session.
fn new_sublayer_key() -> GUID {
    use std::hash::{BuildHasher, Hasher};
    let mut h = std::collections::hash_map::RandomState::new().build_hasher();
    h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
    h.write_u32(std::process::id());
    let a = h.finish();
    let mut h2 = std::collections::hash_map::RandomState::new().build_hasher();
    h2.write_u64(a);
    let b = h2.finish();
    // "Kiwi" in the first four bytes, so the sublayer is recognisable in `netsh wfp show state`
    GUID::from_u128((0x4b69_7769u128 << 96) | ((u128::from(a) << 32) & ((1u128 << 96) - 1)) | u128::from(b as u32))
}
const RPC_C_AUTHN_WINNT: u32 = 10;

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn check(what: &str, code: u32) -> Result<(), String> {
    if code == 0 {
        Ok(())
    } else {
        Err(format!("{what}: Windows Filtering Platform error {code:#x}"))
    }
}

/// The rules in force. Dropping it removes them.
pub struct Guard {
    engine: HANDLE,
    sublayer: GUID,
    kill_switch: bool,
    filters: usize,
}

// The handle is only used from behind the service's lock.
unsafe impl Send for Guard {}

impl Guard {
    pub fn kill_switch(&self) -> bool {
        self.kill_switch
    }
    pub fn filters(&self) -> usize {
        self.filters
    }

    /// `apps` are the programs `Cond::App` refers to, by index. One that doesn't exist is left out, along with its rules.
    pub fn engage(plan: &Plan, apps: &[&Path]) -> Result<Guard, String> {
        let mut name = wide("Kiwi VPN");
        let mut what = wide("Kiwi VPN: no traffic outside the VPN");
        unsafe {
            let mut session: FWPM_SESSION0 = zeroed();
            session.flags = FWPM_SESSION_FLAG_DYNAMIC;
            session.displayData.name = name.as_mut_ptr();
            let mut engine: HANDLE = null_mut();
            check("open", FwpmEngineOpen0(null(), RPC_C_AUTHN_WINNT, null(), &session, &mut engine))?;
            let mut guard = Guard { engine, sublayer: new_sublayer_key(), kill_switch: plan.kill_switch, filters: 0 };
            let mut ids: Vec<*mut FWP_BYTE_BLOB> = Vec::new();
            for app in apps {
                let mut blob: *mut FWP_BYTE_BLOB = null_mut();
                let ok = app.exists() && FwpmGetAppIdFromFileName0(wide(&app.display().to_string()).as_ptr(), &mut blob) == 0;
                ids.push(if ok { blob } else { null_mut() });
            }
            let result = guard.add_all(rules(plan), &ids, name.as_mut_ptr(), what.as_mut_ptr());
            for mut blob in ids {
                if !blob.is_null() {
                    FwpmFreeMemory0(&mut blob as *mut *mut FWP_BYTE_BLOB as *mut *mut c_void);
                }
            }
            // the one Guard is returned as it is: a copy of its handle would be closed when the original dropped, taking the filters with it
            match result {
                Ok(count) => {
                    guard.filters = count;
                    Ok(guard)
                }
                Err(e) => Err(e), // dropping `guard` closes the engine and anything half-added with it
            }
        }
    }

    /// Any rules, held the same way (dynamic session, own sublayer). For test tools: the end-to-end test cuts a VPN server off
    /// with it, because a Windows Firewall rule may not apply where the tests run.
    pub fn engage_rules(list: Vec<Rule>, label: &str) -> Result<Guard, String> {
        let mut name = wide(label);
        let mut what = wide(label);
        unsafe {
            let mut session: FWPM_SESSION0 = zeroed();
            session.flags = FWPM_SESSION_FLAG_DYNAMIC;
            session.displayData.name = name.as_mut_ptr();
            let mut engine: HANDLE = null_mut();
            check("open", FwpmEngineOpen0(null(), RPC_C_AUTHN_WINNT, null(), &session, &mut engine))?;
            let mut guard = Guard { engine, sublayer: new_sublayer_key(), kill_switch: false, filters: 0 };
            guard.filters = guard.add_all(list, &[], name.as_mut_ptr(), what.as_mut_ptr())?;
            Ok(guard)
        }
    }

    unsafe fn add_all(&self, list: Vec<Rule>, apps: &[*mut FWP_BYTE_BLOB], name: *mut u16, what: *mut u16) -> Result<usize, String> {
        check("begin", FwpmTransactionBegin0(self.engine, 0))?;
        let run = || -> Result<usize, String> {
            let mut sublayer: FWPM_SUBLAYER0 = zeroed();
            sublayer.subLayerKey = self.sublayer;
            sublayer.displayData.name = name;
            sublayer.displayData.description = what;
            sublayer.weight = 0xFFFF;
            check("sublayer", FwpmSubLayerAdd0(self.engine, &sublayer, null_mut()))?;
            let mut count = 0;
            for rule in list {
                if rule.conds.iter().any(|c| matches!(c, Cond::App(i) if apps.get(*i).is_none_or(|b| b.is_null()))) {
                    continue;
                }
                add_rule(self.engine, self.sublayer, &rule, apps, name)?;
                count += 1;
            }
            Ok(count)
        };
        match run() {
            Ok(count) => {
                check("commit", FwpmTransactionCommit0(self.engine))?;
                Ok(count)
            }
            Err(e) => {
                FwpmTransactionAbort0(self.engine);
                Err(e)
            }
        }
    }
}

impl Drop for Guard {
    fn drop(&mut self) {
        unsafe { FwpmEngineClose0(self.engine) };
    }
}

fn layer(rule: &Rule) -> GUID {
    match (rule.dir, rule.v6) {
        (Dir::Out, false) => FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        (Dir::Out, true) => FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        (Dir::In, false) => FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V4,
        (Dir::In, true) => FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V6,
    }
}

unsafe fn add_rule(engine: HANDLE, sublayer: GUID, rule: &Rule, apps: &[*mut FWP_BYTE_BLOB], name: *mut u16) -> Result<(), String> {
    // the values the conditions point to live here until WFP has copied them
    let mut v4: Vec<Box<FWP_V4_ADDR_AND_MASK>> = Vec::new();
    let mut v6: Vec<Box<FWP_V6_ADDR_AND_MASK>> = Vec::new();
    let mut conds: Vec<FWPM_FILTER_CONDITION0> = Vec::new();
    for c in &rule.conds {
        let mut fc: FWPM_FILTER_CONDITION0 = zeroed();
        fc.matchType = FWP_MATCH_EQUAL;
        match c {
            Cond::Loopback => {
                fc.fieldKey = FWPM_CONDITION_FLAGS;
                fc.matchType = FWP_MATCH_FLAGS_ALL_SET;
                fc.conditionValue.r#type = FWP_UINT32;
                fc.conditionValue.Anonymous.uint32 = FWP_CONDITION_FLAG_IS_LOOPBACK;
            }
            Cond::App(i) => {
                fc.fieldKey = FWPM_CONDITION_ALE_APP_ID;
                fc.conditionValue.r#type = FWP_BYTE_BLOB_TYPE;
                fc.conditionValue.Anonymous.byteBlob = apps[*i];
            }
            Cond::LocalNet(n) | Cond::RemoteNet(n) => {
                fc.fieldKey = if matches!(c, Cond::LocalNet(_)) { FWPM_CONDITION_IP_LOCAL_ADDRESS } else { FWPM_CONDITION_IP_REMOTE_ADDRESS };
                match n.ip {
                    std::net::IpAddr::V4(_) => {
                        let (addr, mask) = n.v4_parts().unwrap_or((0, 0));
                        let mut b = Box::new(FWP_V4_ADDR_AND_MASK { addr, mask });
                        fc.conditionValue.r#type = FWP_V4_ADDR_MASK;
                        fc.conditionValue.Anonymous.v4AddrMask = &mut *b;
                        v4.push(b);
                    }
                    std::net::IpAddr::V6(a) => {
                        let mut b = Box::new(FWP_V6_ADDR_AND_MASK { addr: a.octets(), prefixLength: n.bits });
                        fc.conditionValue.r#type = FWP_V6_ADDR_MASK;
                        fc.conditionValue.Anonymous.v6AddrMask = &mut *b;
                        v6.push(b);
                    }
                }
            }
            Cond::RemotePort(p) | Cond::LocalPort(p) => {
                fc.fieldKey = if matches!(c, Cond::RemotePort(_)) { FWPM_CONDITION_IP_REMOTE_PORT } else { FWPM_CONDITION_IP_LOCAL_PORT };
                fc.conditionValue.r#type = FWP_UINT16;
                fc.conditionValue.Anonymous.uint16 = *p;
            }
            Cond::Protocol(p) => {
                fc.fieldKey = FWPM_CONDITION_IP_PROTOCOL;
                fc.conditionValue.r#type = FWP_UINT8;
                fc.conditionValue.Anonymous.uint8 = *p;
            }
        }
        conds.push(fc);
    }
    let mut filter: FWPM_FILTER0 = zeroed();
    filter.displayData.name = name;
    filter.layerKey = layer(rule);
    filter.subLayerKey = sublayer;
    filter.weight.r#type = FWP_UINT8;
    filter.weight.Anonymous.uint8 = rule.weight;
    filter.numFilterConditions = conds.len() as u32;
    filter.filterCondition = if conds.is_empty() { null_mut() } else { conds.as_mut_ptr() };
    filter.action.r#type = if rule.permit { FWP_ACTION_PERMIT } else { FWP_ACTION_BLOCK };
    let mut id = 0u64;
    let added = FwpmFilterAdd0(engine, &filter, null_mut(), &mut id);
    drop((v4, v6));
    check(rule.name, added)
}
