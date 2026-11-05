// swift-tools-version:5.9
// The app's logic with no UI and no NetworkExtension, so `swift test` runs it on any Mac, with no iPhone or signing.
import PackageDescription

let package = Package(
    name: "KiwiCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "KiwiCore", targets: ["KiwiCore"])],
    targets: [
        .target(name: "KiwiCore"),
        .testTarget(name: "KiwiCoreTests", dependencies: ["KiwiCore"], resources: [.copy("Fixtures")]),
    ]
)
