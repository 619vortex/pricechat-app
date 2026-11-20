import KiwiCore
import SwiftUI

// The same colours as pricechat.live/vpn, Windows and Android: android-client/docs/DESIGN-SPEC.md.
enum KiwiColor {
    static let ground = Color(red: 7 / 255, green: 11 / 255, blue: 9 / 255)
    static let forest = Color(red: 13 / 255, green: 29 / 255, blue: 21 / 255)
    static let forest2 = Color(red: 18 / 255, green: 40 / 255, blue: 28 / 255)
    static let forest3 = Color(red: 26 / 255, green: 55 / 255, blue: 38 / 255)
    static let lime = Color(red: 163 / 255, green: 230 / 255, blue: 53 / 255)
    static let limeBright = Color(red: 190 / 255, green: 242 / 255, blue: 100 / 255)
    static let ink = Color(red: 11 / 255, green: 20 / 255, blue: 6 / 255)
    static let warn = Color(red: 255 / 255, green: 159 / 255, blue: 90 / 255)
    static let text = Color(red: 246 / 255, green: 248 / 255, blue: 244 / 255)
    static let soft = Color(red: 188 / 255, green: 199 / 255, blue: 191 / 255)
    static let muted = Color(red: 132 / 255, green: 145 / 255, blue: 138 / 255)
}

struct KiwiCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(KiwiColor.forest, in: RoundedRectangle(cornerRadius: 14))
    }
}

struct BetaChip: View {
    var body: some View {
        Text("Public beta").font(.caption.weight(.semibold)).foregroundStyle(KiwiColor.warn)
            .padding(.horizontal, 10).padding(.vertical, 3)
            .background(KiwiColor.warn.opacity(0.12), in: Capsule())
    }
}

struct Fact: View {
    let name: String
    let value: String
    var body: some View {
        HStack(alignment: .top) {
            Text(name).foregroundStyle(KiwiColor.muted)
            Spacer(minLength: 12)
            Text(value).foregroundStyle(KiwiColor.text).multilineTextAlignment(.trailing)
        }.font(.footnote)
    }
}

func flag(_ code: String) -> String {
    let upper = code.uppercased()
    guard upper.count == 2, upper.allSatisfy({ $0.isASCII && $0.isLetter }) else { return "" }
    return String(String.UnicodeScalarView(upper.unicodeScalars.compactMap { Unicode.Scalar(0x1F1E6 + $0.value - 65) }))
}

struct RootView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var connection: ConnectionCoordinator

    var body: some View {
        ZStack {
            KiwiColor.ground.ignoresSafeArea()
            if !model.declarationAccepted {
                DeclarationView(model: model)
            } else if !model.signedIn {
                WelcomeView(model: model)
            } else {
                TabView {
                    HomeView(model: model, connection: connection).tabItem { Label("Home", systemImage: "shield") }
                    LocationsView(model: model, connection: connection).tabItem { Label("Locations", systemImage: "mappin.and.ellipse") }
                    PassportView(model: model).tabItem { Label("Passport", systemImage: "person.text.rectangle") }
                    SettingsView(model: model, connection: connection).tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
                }
            }
        }
    }
}

/// App Store Guideline 5.4: a clear declaration of what's collected and how it's used, on a screen, before the service can be used. Each line must stay true of what the code and backend do: docs/APPLE-CHECKLIST.md.
struct DeclarationView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Before you start").font(.title.bold())
                Text("What Kiwi VPN collects").font(.headline)
                bullet("Your Price Chat account, to sign you in and check your plan.")
                bullet("Each device's name, public key and tunnel address, to run the VPN.")
                bullet("Each device's last connection time and the amount of data it used, to show your use and apply limits.")
                bullet("Your IP address, which the VPN server has to see in order to connect you. Checking your exit address doesn't store it.")
                Text("What it doesn't do").font(.headline)
                bullet("The app doesn't collect the sites you visit.")
                bullet("Your device's private key is made on this iPhone and never leaves it.")
                bullet("Kiwi VPN doesn't sell or share this data with third parties, and uses it only to run the service.")
                Text("This is a public beta. There's no kill switch, and DNS and IPv6 leak testing isn't finished.").font(.footnote).foregroundStyle(KiwiColor.warn)
                Link("Read the privacy policy", destination: URL(string: "\(kiwiBaseURL)/vpn/privacy")!)
                Button { model.acceptDeclaration() } label: { Text("Continue").frame(maxWidth: .infinity).padding(.vertical, 6) }
                    .buttonStyle(.borderedProminent).foregroundStyle(KiwiColor.ink)
            }.padding(24)
        }
    }

    private func bullet(_ text: String) -> some View {
        Label { Text(text).foregroundStyle(KiwiColor.soft) } icon: { Image(systemName: "circle.fill").font(.system(size: 6)).foregroundStyle(KiwiColor.lime) }.font(.subheadline)
    }
}

struct WelcomeView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(spacing: 16) {
            Spacer()
            Image("Kiwi").resizable().scaledToFit().frame(width: 132, height: 132).accessibilityHidden(true)
            Text("KIWI VPN").font(.largeTitle.weight(.black)).foregroundStyle(KiwiColor.limeBright)
            Text("Covers every app on your iPhone. Sign in with your Price Chat account, choose a country and connect.").foregroundStyle(KiwiColor.soft).multilineTextAlignment(.center)
            BetaChip()
            Spacer()
            if model.signingIn {
                ProgressView()
            } else {
                Button { Task { await model.signIn() } } label: { Text("Sign in").frame(maxWidth: .infinity).padding(.vertical, 6) }
                    .buttonStyle(.borderedProminent).foregroundStyle(KiwiColor.ink)
                Text("Opens a secure browser sheet. Kiwi VPN never sees your password.").font(.footnote).foregroundStyle(KiwiColor.muted).multilineTextAlignment(.center)
            }
            if let error = model.error { Text(error).foregroundStyle(KiwiColor.warn).multilineTextAlignment(.center) }
        }.padding(28)
    }
}
