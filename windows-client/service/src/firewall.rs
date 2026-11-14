//! `guard`'s rules as Windows Filtering Platform filters, in a dynamic session: Windows deletes every one of them when the session's
//! handle closes or the process holding it ends, crashed or not. That is the normal kill switch, and it can never outlive Kiwi.
//!
//! 2.2, audit M3: the strict kill switch (opt-in, default off) adds the same rules a second time as PERSISTENT filters in a fixed
//! sublayer, plus a small BOOTTIME set, so the block also holds while the service restarts, before it starts at boot, and through
//! every automatic give-up. Those filters are removed only by `strict_remove`: Disconnect, `kiwivpn-service unblock`, uninstalling,
//! or a connect with strict mode off. Filter keys are fixed (a base GUID plus an index), so they can always be found and deleted,
//! by any copy of the service, without remembering anything.

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

/// The strict kill switch's own sublayer and filter keys ("Kiwi" "STRICT" in the first bytes, as `netsh wfp show state` shows them).
const STRICT_SUBLAYER: GUID = GUID::from_u128(0x4b697769_5354_5249_4354_000000000001);
const STRICT_FILTER_BASE: u128 = 0x4b697769_5354_5249_4354_100000000000;
const BOOT_FILTER_BASE: u128 = 0x4b697769_5354_5249_4354_200000000000;
/// More than any plan makes (64 ranges kept off, the local network, the basics), so a set is always deleted whole.
const MAX_STRICT_FILTERS: u128 = 4096;
const FWP_E_FILTER_NOT_FOUND: u32 = 0x8032_0003;
const FWP_E_SUBLAYER_NOT_FOUND: u32 = 0x8032_0007;
const FWP_E_ALREADY_EXISTS: u32 = 0x8032_0009;

fn strict_key(base: u128, index: usize) -> GUID {
    GUID::from_u128(base + index as u128)
}

/// How a filter is kept: with the session (normal), persistently, or at boot.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Keep {
    Session,
    Persistent(usize),
    Boot(usize),
}

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
    /// Filters at the IP packet layers (every packet judged) instead of the ALE layers (each flow judged once). Test tools only.
    packet: bool,
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
            let mut guard = Guard { engine, sublayer: new_sublayer_key(), kill_switch: plan.kill_switch, filters: 0, packet: false };
            let ids = AppIds::of(apps);
            let result = guard.add_all(rules(plan), &ids.0, name.as_mut_ptr(), what.as_mut_ptr());
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
    /// with it, because a Windows Firewall rule may not apply where the tests run. `packet`: judge every packet (IP packet
    /// layers), so a flow that is already open (the tunnel's own UDP) is cut too; the ALE layers only judge new flows.
    pub fn engage_rules(list: Vec<Rule>, label: &str, packet: bool) -> Result<Guard, String> {
        let mut name = wide(label);
        let mut what = wide(label);
        unsafe {
            let mut session: FWPM_SESSION0 = zeroed();
            session.flags = FWPM_SESSION_FLAG_DYNAMIC;
            session.displayData.name = name.as_mut_ptr();
            let mut engine: HANDLE = null_mut();
            check("open", FwpmEngineOpen0(null(), RPC_C_AUTHN_WINNT, null(), &session, &mut engine))?;
            let mut guard = Guard { engine, sublayer: new_sublayer_key(), kill_switch: false, filters: 0, packet };
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
                add_rule(self.engine, self.sublayer, &rule, apps, name, self.packet, Keep::Session)?;
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

/// The programs' WFP app ids, freed when dropped. A program that doesn't exist gets a null, and its rules are skipped.
struct AppIds(Vec<*mut FWP_BYTE_BLOB>);

impl AppIds {
    fn of(apps: &[&Path]) -> AppIds {
        let mut ids = Vec::new();
        for app in apps {
            let mut blob: *mut FWP_BYTE_BLOB = null_mut();
            let ok = app.exists() && unsafe { FwpmGetAppIdFromFileName0(wide(&app.display().to_string()).as_ptr(), &mut blob) } == 0;
            ids.push(if ok { blob } else { null_mut() });
        }
        AppIds(ids)
    }
}

impl Drop for AppIds {
    fn drop(&mut self) {
        for blob in self.0.iter_mut() {
            if !blob.is_null() {
                unsafe { FwpmFreeMemory0(blob as *mut *mut FWP_BYTE_BLOB as *mut *mut c_void) };
            }
        }
    }
}

/// An engine handle opened without a dynamic session: what it adds with the persistent flag stays after it closes.
struct Engine(HANDLE);

impl Engine {
    fn open() -> Result<Engine, String> {
        let mut engine: HANDLE = null_mut();
        check("open", unsafe { FwpmEngineOpen0(null(), RPC_C_AUTHN_WINNT, null(), null(), &mut engine) })?;
        Ok(Engine(engine))
    }

    /// Runs `work` in one WFP transaction: all of it happens, or none of it.
    fn transaction<T>(&self, work: impl FnOnce(HANDLE) -> Result<T, String>) -> Result<T, String> {
        check("begin", unsafe { FwpmTransactionBegin0(self.0, 0) })?;
        match work(self.0) {
            Ok(v) => {
                check("commit", unsafe { FwpmTransactionCommit0(self.0) })?;
                Ok(v)
            }
            Err(e) => {
                unsafe { FwpmTransactionAbort0(self.0) };
                Err(e)
            }
        }
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        unsafe { FwpmEngineClose0(self.0) };
    }
}

/// Deletes a contiguous set of fixed-key filters, from index 0 until the first one that isn't there.
unsafe fn delete_set(engine: HANDLE, base: u128) -> Result<usize, String> {
    for i in 0..MAX_STRICT_FILTERS as usize {
        let key = strict_key(base, i);
        match FwpmFilterDeleteByKey0(engine, &key) {
            0 => {}
            FWP_E_FILTER_NOT_FOUND => return Ok(i),
            code => return check("delete strict filter", code).map(|_| i),
        }
    }
    Ok(MAX_STRICT_FILTERS as usize)
}

unsafe fn ensure_strict_sublayer(engine: HANDLE, name: *mut u16, what: *mut u16) -> Result<(), String> {
    let mut sublayer: FWPM_SUBLAYER0 = zeroed();
    sublayer.subLayerKey = STRICT_SUBLAYER;
    sublayer.displayData.name = name;
    sublayer.displayData.description = what;
    sublayer.flags = FWPM_SUBLAYER_FLAG_PERSISTENT;
    sublayer.weight = 0xFFFE;
    match FwpmSubLayerAdd0(engine, &sublayer, null_mut()) {
        0 | FWP_E_ALREADY_EXISTS => Ok(()),
        code => check("strict sublayer", code),
    }
}

/// The strict kill switch (audit M3): `plan`'s rules as persistent filters, replacing any earlier strict set in one transaction,
/// so there is never a moment with neither. Then a boot-time set (no program conditions; WFP enforces it from early boot until
/// the Base Filtering Engine starts and the persistent set takes over), best effort: its result is returned for the log.
pub fn strict_engage(plan: &Plan, apps: &[&Path]) -> Result<(usize, Result<usize, String>), String> {
    let mut name = wide("Kiwi VPN strict kill switch");
    let mut what = wide("Kiwi VPN: no traffic outside the VPN, kept until Disconnect or kiwivpn-service unblock");
    let engine = Engine::open()?;
    let ids = AppIds::of(apps);
    let list = rules(plan);
    let count = engine.transaction(|e| unsafe {
        ensure_strict_sublayer(e, name.as_mut_ptr(), what.as_mut_ptr())?;
        delete_set(e, STRICT_FILTER_BASE)?;
        let mut n = 0;
        for rule in &list {
            if rule.conds.iter().any(|c| matches!(c, Cond::App(i) if ids.0.get(*i).is_none_or(|b| b.is_null()))) {
                continue;
            }
            add_rule(e, STRICT_SUBLAYER, rule, &ids.0, name.as_mut_ptr(), false, Keep::Persistent(n))?;
            n += 1;
        }
        Ok(n)
    })?;
    let boot = engine.transaction(|e| unsafe {
        delete_set(e, BOOT_FILTER_BASE)?;
        let mut n = 0;
        for rule in list.iter().filter(|r| !r.conds.iter().any(|c| matches!(c, Cond::App(_)))) {
            add_rule(e, STRICT_SUBLAYER, rule, &[], name.as_mut_ptr(), false, Keep::Boot(n))?;
            n += 1;
        }
        Ok(n)
    });
    Ok((count, boot))
}

/// Removes every strict filter (persistent and boot-time) and the strict sublayer. Nothing to remove is fine.
pub fn strict_remove() -> Result<usize, String> {
    let engine = Engine::open()?;
    engine.transaction(|e| unsafe {
        let n = delete_set(e, STRICT_FILTER_BASE)? + delete_set(e, BOOT_FILTER_BASE)?;
        match FwpmSubLayerDeleteByKey0(e, &STRICT_SUBLAYER) {
            0 | FWP_E_SUBLAYER_NOT_FOUND => Ok(n),
            code => check("strict sublayer", code).map(|_| n),
        }
    })
}

/// True while the strict kill switch's filters are in place (they outlive the service, so a fresh start asks Windows).
pub fn strict_present() -> bool {
    let Ok(engine) = Engine::open() else { return false };
    let key = strict_key(STRICT_FILTER_BASE, 0);
    let mut filter: *mut FWPM_FILTER0 = null_mut();
    let found = unsafe { FwpmFilterGetByKey0(engine.0, &key, &mut filter) } == 0;
    if !filter.is_null() {
        unsafe { FwpmFreeMemory0(&mut filter as *mut *mut FWPM_FILTER0 as *mut *mut c_void) };
    }
    found
}

fn layer(rule: &Rule) -> GUID {
    match (rule.dir, rule.v6) {
        (Dir::Out, false) => FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        (Dir::Out, true) => FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        (Dir::In, false) => FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V4,
        (Dir::In, true) => FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V6,
    }
}

fn packet_layer(rule: &Rule) -> GUID {
    match (rule.dir, rule.v6) {
        (Dir::Out, false) => FWPM_LAYER_OUTBOUND_IPPACKET_V4,
        (Dir::Out, true) => FWPM_LAYER_OUTBOUND_IPPACKET_V6,
        (Dir::In, false) => FWPM_LAYER_INBOUND_IPPACKET_V4,
        (Dir::In, true) => FWPM_LAYER_INBOUND_IPPACKET_V6,
    }
}

unsafe fn add_rule(engine: HANDLE, sublayer: GUID, rule: &Rule, apps: &[*mut FWP_BYTE_BLOB], name: *mut u16, packet: bool, keep: Keep) -> Result<(), String> {
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
            Cond::IcmpType(t) => {
                // FWPM_CONDITION_ICMP_TYPE is defined as FWPM_CONDITION_IP_LOCAL_PORT: ICMP's type travels in the local port field
                fc.fieldKey = FWPM_CONDITION_IP_LOCAL_PORT;
                fc.conditionValue.r#type = FWP_UINT16;
                fc.conditionValue.Anonymous.uint16 = u16::from(*t);
            }
        }
        conds.push(fc);
    }
    let mut filter: FWPM_FILTER0 = zeroed();
    match keep {
        Keep::Session => {}
        Keep::Persistent(i) => {
            filter.filterKey = strict_key(STRICT_FILTER_BASE, i);
            filter.flags = FWPM_FILTER_FLAG_PERSISTENT;
        }
        Keep::Boot(i) => {
            filter.filterKey = strict_key(BOOT_FILTER_BASE, i);
            filter.flags = FWPM_FILTER_FLAG_BOOTTIME;
        }
    }
    filter.displayData.name = name;
    filter.layerKey = if packet { packet_layer(rule) } else { layer(rule) };
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
