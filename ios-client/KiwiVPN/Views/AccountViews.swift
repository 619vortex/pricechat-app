import KiwiCore
import SwiftUI

/// Onboarding, and Settings: what iOS really routes through the VPN. Apple's documentation says per-app VPN can only be set up through MDM, with managed apps, so a personal iPhone can't choose apps. See docs/SELECTIVE-VPN-IOS.md.
struct RoutingExplainerView: View {
    /// Set during onboarding. nil when opened from Settings.
    var onContinue: (() -> Void)?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("What uses Kiwi VPN on iPhone").font(.title.bold())
                Text("Every app.").font(.headline).foregroundStyle(KiwiColor.limeBright)
                point("When Kiwi VPN is on, all the apps on your iPhone use it: Safari, WhatsApp, Telegram, Signal, games and the rest.")
                point("iPhone doesn't let a VPN app choose which other apps use it. Apple allows that only on iPhones managed by an organisation through device management (MDM), not on personal iPhones. So, unlike Kiwi VPN for Android, there's no list of apps here.")
                point("iOS always keeps a little system traffic outside any VPN, such as Wi-Fi sign-in pages and some mobile network services.")
                Text("If you only want it sometimes").font(.headline)
                point("Connect when you need it, and disconnect after.")
                point("On-Demand VPN, in Settings, connects by itself on Wi-Fi, on mobile data, or always.")
                point("A Shortcuts automation can connect when you open an app such as WhatsApp. It follows the app opening: while connected, every app still uses the VPN.")
                NavigationLink { PlatformDifferencesView() } label: {
                    Label("How this differs from Android", systemImage: "info.circle")
                }
                if let onContinue {
                    Button(action: onContinue) { Text("Continue").frame(maxWidth: .infinity).padding(.vertical, 6) }
                        .buttonStyle(.borderedProminent).foregroundStyle(KiwiColor.ink)
                }
            }.padding(24)
        }
        .background(KiwiColor.ground)
        .navigationTitle(onContinue == nil ? "Which apps use it" : "")
    }

    private func point(_ text: String) -> some View {
        Label { Text(text).foregroundStyle(KiwiColor.soft) } icon: { Image(systemName: "circle.fill").font(.system(size: 6)).foregroundStyle(KiwiColor.lime) }.font(.subheadline)
    }
}

/// The Android feature parity note, kept in the app so it's true of this build.
struct PlatformDifferencesView: View {
    var body: some View {
        Form {
            row("Choosing apps", android: "All apps, only the apps you pick, or all except the ones you pick.", iphone: "Always all apps. Apple allows per-app VPN only on iPhones managed by an organisation (MDM).")
            row("Calls", android: "Auto-Call VPN can connect when a WhatsApp or Telegram call starts.", iphone: "Not possible: iPhone apps can't tell that another app is on a call. Connect first, or use On-Demand or a Shortcuts automation.")
            row("Blocking traffic when the VPN drops", android: "Android's own Always-on VPN and Block connections without VPN settings.", iphone: "\"Send all traffic\" uses Apple's includeAllNetworks. iOS still lets some system traffic out, so it isn't a kill switch.")
            row("Connecting by itself", android: "Android's Always-on VPN setting.", iphone: "On-Demand VPN: always, on Wi-Fi, or on mobile data.")
            Section {
                Text("Both apps use the same account, plan, servers and Passport.").foregroundStyle(KiwiColor.soft)
            }
        }
        .scrollContentBackground(.hidden)
        .background(KiwiColor.ground)
        .navigationTitle("iPhone and Android")
    }

    private func row(_ title: String, android: String, iphone: String) -> some View {
        Section(title) {
            Fact(name: "Android", value: android)
            Fact(name: "iPhone", value: iphone)
        }
    }
}

/// App Store Guideline 5.1.1(v): deleting the account from inside the app. What's listed here is what the backend's DELETE /members/me and the privacy policy say. See docs/APP-STORE-SUBMISSION.md.
struct DeleteAccountView: View {
    @ObservedObject var model: AppModel
    @State private var typed = ""
    @State private var working = false
    @State private var problem: String?
    @State private var confirming = false

    private var ready: Bool { AccountDeletion.matches(typed: typed, username: model.username) && !working }

    var body: some View {
        Form {
            Section {
                Text("This deletes your whole Price Chat account, not only Kiwi VPN.").font(.headline)
                line("Your Kiwi Passport: profile, sign-in methods, sessions on every device, XP history, rank and badges.")
                line("Kiwi VPN: your plan and any time left, every device on every platform, browser extension logins, and support reports. Your other devices stop working once each server next updates.")
                line("On this iPhone: the VPN configuration, your session and the WireGuard keys.")
            } header: {
                Text("What's deleted")
            }
            Section {
                line("Payment records, no longer linked to you, for as long as tax law requires.")
                line("One-way fingerprints of your sign-in identities, so a new account can't take another free trial.")
                line("A record that an account number was deleted, with no name or contact details, and anonymous entries in shared records such as paper trading.")
                line("Our hosting providers' request logs, for as long as they keep them.")
                Link("Read the privacy policy", destination: URL(string: "\(kiwiBaseURL)/vpn/privacy")!)
            } header: {
                Text("What's kept")
            }
            Section {
                if let username = model.username {
                    Text("Type \(username) to confirm.").font(.footnote).foregroundStyle(KiwiColor.soft)
                    TextField("Username", text: $typed).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Delete my account", role: .destructive) { confirming = true }.disabled(!ready)
                } else {
                    Text("Your username hasn't loaded yet. Check your connection, then come back.").foregroundStyle(KiwiColor.soft)
                }
                if working { ProgressView("Deleting") }
                if let problem { Text(problem).font(.footnote).foregroundStyle(KiwiColor.warn) }
            } header: {
                Text("Confirm")
            } footer: {
                Text("This can't be undone.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(KiwiColor.ground)
        .navigationTitle("Delete account")
        .confirmationDialog("Delete your Price Chat account?", isPresented: $confirming, titleVisibility: .visible) {
            Button("Delete account", role: .destructive) {
                Task {
                    working = true
                    problem = nil
                    problem = await model.deleteAccount(typed: typed)
                    working = false
                }
            }
        } message: {
            Text("Kiwi VPN disconnects now, and your account, Passport and devices are deleted.")
        }
    }

    private func line(_ text: String) -> some View {
        Text(text).font(.footnote).foregroundStyle(KiwiColor.soft)
    }
}

/// The notices the open-source licences require in the app's binary distribution.
struct LicencesView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                notice("WireGuardKit and wireguard-go", "Copyright © 2017-2023 WireGuard LLC. All Rights Reserved.", mit)
                notice("The Go runtime and golang.org/x libraries, compiled into the tunnel", "Copyright (c) 2009 The Go Authors. All rights reserved.", bsd)
            }.padding(20)
        }
        .background(KiwiColor.ground)
        .navigationTitle("Licences")
    }

    private func notice(_ title: String, _ copyright: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            Text(copyright).font(.footnote)
            Text(text).font(.caption).foregroundStyle(KiwiColor.soft)
        }
    }

    private let mit = """
    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
    """

    private let bsd = """
    Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
    * Neither the name of Google Inc. nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
    """
}
