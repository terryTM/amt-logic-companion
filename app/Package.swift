// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AMTGenerator",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "AMTGenerator",
            path: "Sources/AMTGenerator",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("SwiftUI"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("UniformTypeIdentifiers"),
            ]
        )
    ]
)
