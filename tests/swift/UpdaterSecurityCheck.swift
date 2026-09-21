import Foundation

private enum CheckError: Error, CustomStringConvertible {
    case failed(String)

    var description: String {
        switch self {
        case .failed(let message): return message
        }
    }
}

@main
private enum UpdaterSecurityCheck {
    static func main() throws {
        let digest = String(repeating: "a", count: 64)

        try testUpdateWorkspaceAndInstaller()
        try testShellEscaping()
        try testVersionSelection(digest: digest)

        try expect(UpdateSecurity.isAllowedMetadataURL(
            try url("https://api.github.com/repos/hi-unc1e/tokei/releases/latest")
        ), "GitHub metadata URL should be allowed")
        try expect(UpdateSecurity.isAllowedDownloadSourceURL(
            try url("https://github.com/hi-unc1e/tokei/releases/download/v1.0.14/Tokei.dmg")
        ), "GitHub release asset should be allowed")
        try expect(UpdateSecurity.isAllowedDownloadResponseURL(
            try url("https://release-assets.githubusercontent.com/github-production-release-asset/file")
        ), "GitHub release redirect should be allowed")

        try expect(!UpdateSecurity.isAllowedDownloadSourceURL(
            try url("http://github.com/hi-unc1e/tokei/releases/download/v1.0.14/Tokei.dmg")
        ), "plain HTTP should be rejected")
        try expect(!UpdateSecurity.isAllowedDownloadSourceURL(
            try url("https://github.com.evil.example/Tokei.dmg")
        ), "lookalike subdomain should be rejected")
        try expect(!UpdateSecurity.isAllowedDownloadSourceURL(
            try url("https://github.com@evil.example/Tokei.dmg")
        ), "userinfo host confusion should be rejected")
        try expect(!UpdateSecurity.isAllowedDownloadSourceURL(
            try url("https://github.com:444/hi-unc1e/tokei/Tokei.dmg")
        ), "unexpected port should be rejected")

        let githubJSON: [String: Any] = [
            "tag_name": "v1.0.14",
            "url": "https://api.github.com/repos/hi-unc1e/tokei/releases/1",
            "assets": [[
                "name": "Tokei.dmg",
                "browser_download_url": "https://github.com/hi-unc1e/tokei/releases/download/v1.0.14/Tokei.dmg",
                "digest": "sha256:\(digest.uppercased())",
            ]],
        ]
        let githubRelease = try unwrap(UpdateSecurity.validatedRelease(from: githubJSON),
                                       "GitHub release should parse")
        try expect(githubRelease.tag == "v1.0.14", "GitHub tag should be preserved")
        try expect(githubRelease.downloadURL.host == "github.com", "DMG asset URL should win over API URL")
        try expect(githubRelease.sha256 == digest, "GitHub digest should be normalized")

        let mirrorJSON: [String: Any] = [
            "version": "v1.0.14",
            "download_url": "https://github.com/hi-unc1e/tokei/releases/download/v1.0.14/Tokei-v1.0.14.dmg",
            "sha256": digest,
        ]
        try expect(UpdateSecurity.validatedRelease(from: mirrorJSON)?.sha256 == digest,
                   "release metadata with a digest should parse")
        try expect(UpdateSecurity.validatedRelease(from: [
            "tag_name": "v1.0.14",
            "download_url": "https://github.com/hi-unc1e/tokei/releases/download/v1.0.14/Tokei-v1.0.14.dmg",
        ]) == nil, "missing digest should be rejected")
        try expect(UpdateSecurity.validatedRelease(from: [
            "tag_name": "v1.0.14",
            "download_url": "https://evil.example/Tokei-v1.0.14.dmg",
            "sha256": digest,
        ]) == nil, "untrusted download host should be rejected")
        try expect(UpdateSecurity.normalizedSHA256("sha256:not-a-digest") == nil,
                   "malformed digest should be rejected")

        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("tokei-update-security-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: fileURL) }
        try Data("abc".utf8).write(to: fileURL)
        try expect(
            try UpdateSecurity.sha256(of: fileURL)
                == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "downloaded file SHA-256 should match"
        )

        print("Updater security checks passed")
    }

    private static func testUpdateWorkspaceAndInstaller() throws {
        let fileManager = FileManager.default
        let testRoot = fileManager.temporaryDirectory
            .appendingPathComponent("tokei-installer-test-\(UUID().uuidString)", isDirectory: true)
        try fileManager.createDirectory(at: testRoot, withIntermediateDirectories: false)
        defer { try? fileManager.removeItem(at: testRoot) }

        let workspace = try UpdateInstaller.createWorkspace(in: testRoot)
        let secondWorkspace = try UpdateInstaller.createWorkspace(in: testRoot)
        try expect(workspace.rootURL != secondWorkspace.rootURL,
                   "update workspaces should be unique")
        let attributes = try fileManager.attributesOfItem(atPath: workspace.rootURL.path)
        let permissions = (attributes[.posixPermissions] as? NSNumber)?.intValue ?? -1
        try expect(permissions & 0o777 == 0o700,
                   "update workspace should be private")
        try expect(!UpdateInstaller.script.contains("/tmp/tokei_"),
                   "installer should not use shared fixed paths")
        try fileManager.removeItem(at: secondWorkspace.rootURL)

        let sourceRoot = testRoot.appendingPathComponent("dmg-source", isDirectory: true)
        let sourceApp = sourceRoot.appendingPathComponent("Tokei.app", isDirectory: true)
        try createSignedApp(at: sourceApp, version: "new")

        let installedApp = testRoot.appendingPathComponent("Installed Tokei.app", isDirectory: true)
        try fileManager.createDirectory(at: installedApp, withIntermediateDirectories: true)
        try Data("old".utf8).write(to: installedApp.appendingPathComponent("version.txt"))

        let createStatus = try run(
            "/usr/bin/hdiutil",
            ["create", "-volname", "TokeiTest", "-srcfolder", sourceRoot.path,
             "-ov", "-format", "UDZO", workspace.dmgURL.path]
        )
        try expect(createStatus == 0, "test DMG should be created")

        try UpdateInstaller.script.write(
            to: workspace.scriptURL,
            atomically: true,
            encoding: .utf8
        )
        let backupURL = workspace.backupURL(for: installedApp)
        let installStatus = try run(
            "/bin/bash",
            [workspace.scriptURL.path, workspace.dmgURL.path, workspace.mountURL.path,
             installedApp.path, workspace.rootURL.path, backupURL.path]
        )
        try expect(installStatus == 0, "installer should replace a valid app")
        let installedVersion = try String(
            contentsOf: installedApp.appendingPathComponent("Contents/Resources/version.txt"),
            encoding: .utf8
        )
        try expect(installedVersion == "new", "installer should copy the new app")
        try expect(!fileManager.fileExists(atPath: workspace.rootURL.path),
                   "installer should remove its workspace")
        try expect(!fileManager.fileExists(atPath: backupURL.path),
                   "installer should remove its backup after success")

        let invalidWorkspace = try UpdateInstaller.createWorkspace(in: testRoot)
        let invalidRoot = testRoot.appendingPathComponent("invalid-dmg-source", isDirectory: true)
        let invalidApp = invalidRoot.appendingPathComponent("Tokei.app", isDirectory: true)
        try createSignedApp(at: invalidApp, version: "tampered")
        try Data("modified after signing".utf8).write(
            to: invalidApp.appendingPathComponent("Contents/Resources/version.txt")
        )
        let invalidDMGStatus = try run(
            "/usr/bin/hdiutil",
            ["create", "-volname", "TokeiInvalidTest", "-srcfolder", invalidRoot.path,
             "-ov", "-format", "UDZO", invalidWorkspace.dmgURL.path]
        )
        try expect(invalidDMGStatus == 0, "tampered test DMG should be created")
        try UpdateInstaller.script.write(
            to: invalidWorkspace.scriptURL,
            atomically: true,
            encoding: .utf8
        )
        let invalidStatus = try run(
            "/bin/bash",
            [invalidWorkspace.scriptURL.path, invalidWorkspace.dmgURL.path,
             invalidWorkspace.mountURL.path, installedApp.path, invalidWorkspace.rootURL.path,
             invalidWorkspace.backupURL(for: installedApp).path]
        )
        try expect(invalidStatus != 0, "installer should reject a tampered app")
        let preservedVersion = try String(
            contentsOf: installedApp.appendingPathComponent("Contents/Resources/version.txt"),
            encoding: .utf8
        )
        try expect(preservedVersion == "new", "rejected update should preserve the installed app")
    }

    private static func createSignedApp(at appURL: URL, version: String) throws {
        let fileManager = FileManager.default
        let macOSURL = appURL.appendingPathComponent("Contents/MacOS", isDirectory: true)
        let resourcesURL = appURL.appendingPathComponent("Contents/Resources", isDirectory: true)
        try fileManager.createDirectory(at: macOSURL, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: resourcesURL, withIntermediateDirectories: true)
        let executableURL = macOSURL.appendingPathComponent("Tokei")
        let sourceURL = appURL.deletingLastPathComponent().appendingPathComponent(
            "tokei-test-main-\(UUID().uuidString).c"
        )
        defer { try? fileManager.removeItem(at: sourceURL) }
        try Data("#include <unistd.h>\nint main(void) { sleep(2); return 0; }\n".utf8)
            .write(to: sourceURL)
        let compileStatus = try run(
            "/usr/bin/xcrun",
            ["clang", sourceURL.path, "-o", executableURL.path]
        )
        try expect(compileStatus == 0, "test app executable should compile")
        try fileManager.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executableURL.path)
        try Data(version.utf8).write(to: resourcesURL.appendingPathComponent("version.txt"))

        let plist: [String: Any] = [
            "CFBundleName": "Tokei",
            "CFBundleIdentifier": "com.tokei.app",
            "CFBundleExecutable": "Tokei",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "LSUIElement": true,
        ]
        let plistData = try PropertyListSerialization.data(
            fromPropertyList: plist,
            format: .xml,
            options: 0
        )
        try plistData.write(to: appURL.appendingPathComponent("Contents/Info.plist"))
        let signProcess = Process()
        signProcess.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
        signProcess.arguments = ["--force", "--deep", "--sign", "-", appURL.path]
        let signError = Pipe()
        signProcess.standardOutput = FileHandle.nullDevice
        signProcess.standardError = signError
        try signProcess.run()
        signProcess.waitUntilExit()
        let errorText = String(
            data: signError.fileHandleForReading.readDataToEndOfFile(),
            encoding: .utf8
        ) ?? ""
        try expect(signProcess.terminationStatus == 0,
                   "test app should be ad-hoc signed: \(errorText)")
    }

    private static func testVersionSelection(digest: String) throws {
        try expect(UpdateSecurity.isNewerVersion("v1.0.15", than: "v1.0.14"),
                   "new patch version should be newer")
        try expect(UpdateSecurity.isNewerVersion("v1.1", than: "v1.0.99"),
                   "new minor version should be newer")
        try expect(!UpdateSecurity.isNewerVersion("v1.0.14", than: "v1.0.14"),
                   "equal versions should not be newer")
        try expect(!UpdateSecurity.isNewerVersion("not-a-version", than: "v1.0.14"),
                   "malformed versions should be rejected")

        let older = UpdateRelease(
            tag: "v1.0.15",
            downloadURL: try url("https://github.com/cclank/tokei/releases/download/v1.0.15/Tokei.dmg"),
            sha256: digest
        )
        let newer = UpdateRelease(
            tag: "v1.1.0",
            downloadURL: try url("https://github.com/hi-unc1e/tokei/releases/download/v1.1.0/Tokei.dmg"),
            sha256: digest
        )
        try expect(
            UpdateSecurity.newestRelease(in: [older, newer], newerThan: "v1.0.14") == newer,
            "newest valid release should win across metadata sources"
        )
        try expect(
            UpdateSecurity.newestRelease(in: [older], newerThan: "v2.0.0") == nil,
            "older releases should be ignored"
        )
    }

    private static func testShellEscaping() throws {
        let marker = FileManager.default.temporaryDirectory
            .appendingPathComponent("tokei-shell-marker-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: marker) }
        let dangerous = "/tmp/a'b $(/usr/bin/touch \(marker.path)) `echo injected` $HOME\nnext"
        let quoted = ShellEscaping.singleQuoted(dangerous)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-c", "printf '%s' \(quoted)"]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        try expect(process.terminationStatus == 0, "quoted shell value should parse")
        try expect(String(data: data, encoding: .utf8) == dangerous,
                   "quoted shell value should remain unchanged")
        try expect(!FileManager.default.fileExists(atPath: marker.path),
                   "quoted shell value should not execute substitutions")
    }

    private static func run(_ executable: String, _ arguments: [String]) throws -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        return process.terminationStatus
    }

    private static func expect(_ condition: @autoclosure () throws -> Bool,
                               _ message: String) throws {
        guard try condition() else { throw CheckError.failed(message) }
    }

    private static func url(_ value: String) throws -> URL {
        try unwrap(URL(string: value), "invalid test URL: \(value)")
    }

    private static func unwrap<T>(_ value: T?, _ message: String) throws -> T {
        guard let value else { throw CheckError.failed(message) }
        return value
    }
}
