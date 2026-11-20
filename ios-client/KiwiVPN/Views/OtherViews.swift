import KiwiCore
import SwiftUI
import UIKit

struct LocationsView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var connection: ConnectionCoordinator

    private var current: Int64? { connection.state.isActive ? connection.state.server?.id : model.selected?.id }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Locations").font(.title.bold())
                let servers = model.account?.servers ?? []
                if servers.isEmpty { Text(model.checked ? (model.error ?? "No servers are available right now.") : "Loading servers").foregroundStyle(KiwiColor.soft) }
                ForEach(servers) { server in
                    Button { Task { await model.select(server) } } label: { row(server) }
                        .buttonStyle(.plain)
                        .disabled(!server.online || server.id == current)
                }
                Text("Only what the server reports is shown. No speed or load figures are estimated.").font(.footnote).foregroundStyle(KiwiColor.muted)
            }.padding(20)
        }
    }

    private func row(_ server: Server) -> some View {
        let here = server.id == current
        return HStack(spacing: 12) {
            Text(flag(server.countryCode)).font(.title)
            VStack(alignment: .leading) {
                Text(Locale.current.localizedString(forRegionCode: server.countryCode) ?? server.name).font(.headline)
                Text([server.name, server.region].filter { !$0.isEmpty }.joined(separator: " · ")).font(.footnote).foregroundStyle(KiwiColor.soft)
            }
            Spacer()
            Text(!server.online ? "Offline" : here ? "Selected" : server.recommended ? "Recommended" : "Online")
                .font(.caption.weight(.semibold)).foregroundStyle(server.online ? KiwiColor.limeBright : KiwiColor.warn)
        }
        .padding(14)
        .background(here ? KiwiColor.forest2 : KiwiColor.forest, in: RoundedRectangle(cornerRadius: 14))
    }
}

struct PassportView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Kiwi Passport").font(.title.bold())
                if let p = model.passport {
                    KiwiCard {
                        HStack(spacing: 14) {
                            Image("Kiwi").resizable().scaledToFit().frame(width: 72, height: 72).accessibilityHidden(true)
                            VStack(alignment: .leading) {
                                Text(p.displayName.isEmpty ? p.username : p.displayName).font(.headline)
                                Text("\(p.rank.current.name), level \(p.rank.current.level)").font(.footnote).foregroundStyle(KiwiColor.limeBright)
                            }
                        }
                        ProgressView(value: min(1, max(0, p.rank.progress))).tint(KiwiColor.lime)
                        Fact(name: "XP", value: p.xp.formatted())
                        if let next = p.rank.next { Fact(name: "To \(next.name)", value: "\(p.rank.xpToNext.formatted()) points") }
                        if let standing = p.standing { Fact(name: "Leaderboard", value: "#\(standing.position)") }
                    }
                    KiwiCard {
                        Text("Achievements").font(.headline)
                        if p.badges.isEmpty { Text("None yet.").font(.footnote).foregroundStyle(KiwiColor.soft) }
                        ForEach(p.badges) { badge in
                            VStack(alignment: .leading) {
                                Text(badge.name).font(.subheadline.weight(.semibold))
                                if !badge.description.isEmpty { Text(badge.description).font(.footnote).foregroundStyle(KiwiColor.soft) }
                            }
                        }
                    }
                    if let invite = p.referral, let url = URL(string: invite.link) {
                        KiwiCard {
                            Text("Invite friends").font(.headline)
                            Fact(name: "Friends", value: "\(invite.joined) joined, \(invite.qualified) verified")
                            ShareLink("Share your invite link", item: url)
                        }
                    }
                    // Only ever what the server sends. It sends none until missions are approved, and XP is only ever awarded by the server.
                    if !model.missions.isEmpty {
                        KiwiCard {
                            Text("Missions").font(.headline)
                            ForEach(model.missions) { Fact(name: $0.title, value: "+\($0.xp) XP") }
                        }
                    }
                } else {
                    KiwiCard {
                        Text(model.who ?? "Your Passport").font(.headline)
                        Text("Passport isn't available in the app yet. Your XP and level are safe, and you can see them on the website.").font(.footnote).foregroundStyle(KiwiColor.soft)
                        Link("Open your Passport", destination: URL(string: "\(kiwiBaseURL)/passport")!)
                    }
                }
            }.padding(20)
        }
    }
}

/// What iOS allows instead of Auto-Call VPN. The Shortcuts steps use iOS's own automations with this app's Connect and Disconnect actions: iOS runs them, and they know nothing about calls.
struct AutoCallLimitsView: View {
    var body: some View {
        Form {
            Section("Why it isn't available") {
                Text("iOS gives apps no way to know that another app is on a call, and apps can't read other apps' notifications. A suspended app also isn't woken when a call starts. Android's Auto-Call VPN relies on notification access, which iPhone doesn't offer.")
            }
            Section("What you can do") {
                Text("Connect before you call. Or use On-Demand VPN, which keeps the VPN on for the networks you choose, so a call is covered from its start.")
            }
            Section {
                Text("1. Open the Shortcuts app, then Automation, and add a new automation.")
                Text("2. Choose App, pick WhatsApp or Telegram, and choose Is Opened.")
                Text("3. Choose Run Immediately, then add the action Connect Kiwi VPN.")
                Text("4. If you want it to stop too, make a second automation with Is Closed and Disconnect Kiwi VPN.")
            } header: {
                Text("Connect when you open an app")
            } footer: {
                Text("This follows the app being opened, not a call: a call arriving while the app is closed won't start it, and it also runs when you only open the app to read messages. The Disconnect automation ends the VPN even if you started it yourself. Not yet tested on a real iPhone.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(KiwiColor.ground)
        .navigationTitle("Auto-Call VPN")
    }
}

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var connection: ConnectionCoordinator
    @State private var dnsText = ""
    @State private var mtuText = ""
    @State private var problem: String?
    @State private var confirmRemove: Device?

    var body: some View {
        NavigationStack {
            Form {
                accountSection
                onDemandSection
                autoCallSection
                advancedSection
                Section {
                    NavigationLink("Connection diagnostics") { DiagnosticsView(connection: connection) }
                    if model.capabilities[Capability.gamingMode] == true {
                        NavigationLink("Gaming Mode") { GamingModeView() }
                    }
                    Link("Privacy policy", destination: URL(string: "\(kiwiBaseURL)/vpn/privacy")!)
                    Link("Support", destination: URL(string: "\(kiwiBaseURL)/contact")!)
                    Fact(name: "App version", value: Kiwi.version)
                    if !Kiwi.commit.isEmpty { Fact(name: "Build", value: String(Kiwi.commit.prefix(12))) }
                }
                Section {
                    Button("Sign out", role: .destructive) { Task { await model.signOut() } }
                }
            }
            .scrollContentBackground(.hidden)
            .background(KiwiColor.ground)
            .navigationTitle("Settings")
            .onAppear {
                dnsText = model.settings.customDNS.joined(separator: ", ")
                mtuText = model.settings.mtu.map(String.init) ?? ""
            }
            .confirmationDialog("Remove this device?", isPresented: Binding(get: { confirmRemove != nil }, set: { if !$0 { confirmRemove = nil } }), presenting: confirmRemove) { device in
                Button("Remove", role: .destructive) { Task { await model.removeDevice(device.id) } }
            } message: { _ in
                Text("It stops working straight away. If it's another phone or PC, it will need setting up again.")
            }
        }
    }

    private var accountSection: some View {
        Section("Account") {
            let status = AccountStatus.of(model.account)
            Text(model.who ?? "Price Chat account")
            Text(status.label).foregroundStyle(KiwiColor.limeBright)
            if model.account?.payments == false { Text("Payments aren't available yet.").font(.footnote).foregroundStyle(KiwiColor.muted) }
            if let account = model.account {
                ForEach(account.devices) { device in
                    HStack {
                        Text(device.name).font(.footnote)
                        Spacer()
                        if device.serverId != connection.state.link.flatMap({ _ in connection.state.server?.id }) {
                            Button("Remove") { confirmRemove = device }.font(.footnote).buttonStyle(.borderless)
                        }
                    }
                }
                Text("\(account.devices.count) of \(account.maxDevices) devices. Each country a device uses counts as one.").font(.footnote).foregroundStyle(KiwiColor.muted)
            }
        }
    }

    private var onDemandSection: some View {
        Section {
            Picker("Connect automatically", selection: Binding(get: { model.settings.onDemand }, set: { value in change { $0.onDemand = value } })) {
                Text("Off").tag(AdvancedSettings.OnDemand.off)
                Text("Always").tag(AdvancedSettings.OnDemand.always)
                Text("On Wi-Fi").tag(AdvancedSettings.OnDemand.wifi)
                Text("On mobile data").tag(AdvancedSettings.OnDemand.cellular)
            }
        } header: {
            Text("On-Demand VPN")
        } footer: {
            Text("iOS starts the VPN by itself on the networks you choose, using saved details. Pressing Disconnect turns this off, or iOS would reconnect straight away. Not yet tested on a real iPhone.")
        }
    }

    /// Not a feature switch: there's nothing iOS allows here to switch on. It says so, and points to what iOS does allow.
    private var autoCallSection: some View {
        Section {
            NavigationLink("Auto-Call VPN on iPhone") { AutoCallLimitsView() }
        } header: {
            Text("Calls")
        } footer: {
            Text("iPhone apps can't detect WhatsApp or Telegram calls, so Kiwi VPN can't connect by itself for a call on iPhone.")
        }
    }

    private var advancedSection: some View {
        Section {
            Toggle("Send all traffic through the VPN", isOn: Binding(get: { model.settings.routeAllTraffic }, set: { value in change { $0.routeAllTraffic = value } }))
            if model.settings.routeAllTraffic {
                Toggle("Allow local network devices", isOn: Binding(get: { model.settings.allowLocalNetwork }, set: { value in change { $0.allowLocalNetwork = value } }))
            }
            TextField("DNS servers (automatic if empty)", text: $dnsText).keyboardType(.numbersAndPunctuation).autocorrectionDisabled().textInputAutocapitalization(.never)
            TextField("MTU (automatic if empty)", text: $mtuText).keyboardType(.numberPad)
            Button("Apply DNS and MTU") { applyTyped() }
            if let problem { Text(problem).font(.footnote).foregroundStyle(KiwiColor.warn) }
        } header: {
            Text("Advanced")
        } footer: {
            Text("\"Send all traffic\" is Apple's includeAllNetworks. iOS still keeps some system traffic outside it, such as captive portals and cellular services, so it isn't a kill switch, and it hasn't been tested. A change reconnects the VPN if it's on. IPv6 is always routed into the tunnel, and isn't a setting.")
        }
    }

    private func change(_ edit: (inout AdvancedSettings) -> Void) {
        var next = model.settings
        edit(&next)
        Task { await model.updateSettings(next) }
    }

    private func applyTyped() {
        var next = model.settings
        next.customDNS = dnsText.split(whereSeparator: { $0 == "," || $0 == " " }).map(String.init)
        let mtu = mtuText.trimmingCharacters(in: .whitespaces)
        if mtu.isEmpty {
            next.mtu = nil
        } else if let value = Int(mtu) {
            next.mtu = value
        } else {
            problem = "MTU must be a number."
            return
        }
        problem = next.problem
        if problem == nil { Task { await model.updateSettings(next) } }
    }
}

struct DiagnosticsView: View {
    @ObservedObject var connection: ConnectionCoordinator

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let link = connection.state.link
            Form {
                Section("Tunnel, as iOS and WireGuard report it") {
                    Fact(name: "State", value: connection.state.word)
                    Fact(name: "Server", value: connection.state.server?.name ?? "None")
                    Fact(name: "Endpoint", value: link?.endpoint ?? "None")
                    Fact(name: "Last handshake", value: link?.lastHandshake.map { "\(max(0, Int(context.date.timeIntervalSince($0)))) s ago" } ?? "None")
                    Fact(name: "Received", value: ByteCountFormatter.string(fromByteCount: Int64(link?.rxBytes ?? 0), countStyle: .binary))
                    Fact(name: "Sent", value: ByteCountFormatter.string(fromByteCount: Int64(link?.txBytes ?? 0), countStyle: .binary))
                    Fact(name: "Public IPv4", value: link?.publicIP.map { "\($0) (\(link?.ipMatchesServer == true ? "the server's" : "not the server's"))" } ?? "Not checked")
                    Fact(name: "DNS", value: link?.dns.joined(separator: ", ") ?? "None")
                    if case .failed(let message, _) = connection.state { Fact(name: "Error", value: message) }
                }
                Section {
                    Text("This shows settings, not whether anything leaks. See the DNS and IPv6 tests in the test plan.").font(.footnote).foregroundStyle(KiwiColor.muted)
                }
            }
            .scrollContentBackground(.hidden)
            .background(KiwiColor.ground)
        }
        .navigationTitle("Diagnostics")
    }
}

/// Reachable only if the server's capability is on, and that's off everywhere. It shows no figure, because nothing measures yet, and no number may be shown without a real measurement behind it.
struct GamingModeView: View {
    var body: some View {
        Form {
            Text("Gaming Mode isn't finished. It will show measured latency, jitter and packet loss for each route once those can really be measured. Kiwi VPN doesn't claim to lower your ping.").foregroundStyle(KiwiColor.soft)
        }
        .scrollContentBackground(.hidden)
        .background(KiwiColor.ground)
        .navigationTitle("Gaming Mode")
    }
}
