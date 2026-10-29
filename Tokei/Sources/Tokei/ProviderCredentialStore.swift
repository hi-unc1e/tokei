import Foundation
import Darwin
import LocalAuthentication
import Security

enum ProviderSecret: String {
    case sub2api
    case zai
    case minimax
}

enum ProviderCredentialStore {
#if TOKEI_PROVIDER_CREDENTIAL_STORE_TEST
    // The test binary is compiled with a separate service so its round-trip
    // checks can never touch a user's real provider credentials.
    private static let service = "com.tokei.app.provider-api-key.test." + UUID().uuidString
    private static let testKeychainURL = FileManager.default.temporaryDirectory
        .appendingPathComponent("tokei-provider-" + UUID().uuidString + ".keychain-db")
    private static let testKeychain: SecKeychain = {
        let password = Data("tokei-provider-test".utf8)
        var keychain: SecKeychain?
        let status = testKeychainURL.path.withCString { path in
            password.withUnsafeBytes { bytes in
                SecKeychainCreate(
                    path,
                    UInt32(password.count),
                    bytes.baseAddress,
                    false,
                    nil,
                    &keychain
                )
            }
        }
        guard status == errSecSuccess, let keychain else {
            fatalError("Unable to create isolated provider test keychain: \(status)")
        }
        return keychain
    }()
#else
    // v2 avoids inaccessible items created by older ad-hoc builds whose ACL is
    // tied to a one-off code hash. Do not probe the old service: macOS can block
    // indefinitely while decrypting those items even when UI is disabled.
    private static let service = "com.tokei.app.provider-api-key.v2"
#endif
    private static let interactionLock = NSLock()
    private static let clearedMarker = "__TOKEI_PROVIDER_CLEARED_V1__"

#if TOKEI_PROVIDER_CREDENTIAL_STORE_TEST
    static func purgeTestItems() {
        for provider in [ProviderSecret.sub2api, .zai, .minimax] {
            _ = SecItemDelete(baseQuery(for: provider, service: service) as CFDictionary)
        }
    }

    static func destroyTestKeychain() {
        _ = SecKeychainDelete(testKeychain)
        try? FileManager.default.removeItem(at: testKeychainURL)
    }
#endif

    private enum TokenLookup {
        case missing
        case cleared
        case value(String)
    }

    static func runIfRequested() -> Bool {
        if CommandLine.arguments.contains("--zed-authorize") {
            let ok = zedCredentials(allowInteraction: true) != nil
            print(ok ? "ok" : "unavailable")
            exit(ok ? 0 : 2)
        }
        if CommandLine.arguments.contains("--zed-verify") {
            let ok = zedCredentials() != nil
            print(ok ? "ok" : "unavailable")
            exit(ok ? 0 : 2)
        }
        return false
    }

    static func token(for provider: ProviderSecret) -> String? {
        switch tokenLookup(for: provider, service: service) {
        case .value(let value): return value
        case .cleared, .missing: return nil
        }
    }

    private static func tokenLookup(
        for provider: ProviderSecret,
        service: String
    ) -> TokenLookup {
        var query = baseQuery(for: provider, service: service)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        applyNoUI(to: &query)
        var result: AnyObject?
        let status = withoutKeychainUI { SecItemCopyMatching(query as CFDictionary, &result) }
        guard status == errSecSuccess,
              let data = result as? Data else { return .missing }
        guard let rawValue = String(data: data, encoding: .utf8) else { return .cleared }
        if rawValue == clearedMarker { return .cleared }
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
              !value.isEmpty else { return .cleared }
        return .value(value)
    }

    @discardableResult
    static func setToken(_ token: String, for provider: ProviderSecret) -> Bool {
        invalidateOverridesCache()
        let value = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty {
            let currentStatus = writeTokenData(
                Data(clearedMarker.utf8),
                for: provider, service: service, createIfMissing: true
            )
            return currentStatus == errSecSuccess
        }
        return writeTokenData(
            Data(value.utf8), for: provider, service: service, createIfMissing: true
        ) == errSecSuccess
    }

    private static func writeTokenData(
        _ data: Data,
        for provider: ProviderSecret,
        service: String,
        createIfMissing: Bool
    ) -> OSStatus {
        var query = baseQuery(for: provider, service: service)
        applyNoUI(to: &query)
        let update = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecSuccess || !createIfMissing { return status }
        guard status == errSecItemNotFound else { return status }
        var item = baseQuery(for: provider, service: service)
        targetTestKeychainForAdd(&item)
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        applyNoUI(to: &item)
        return SecItemAdd(item as CFDictionary, nil)
    }

    // 每次刷新都要构建一次环境变量,而 Keychain 查询走的是可能「无限期阻塞」
    // 的 legacy 路径(见上方 service 注释)——2026-10 实测:钥匙串锁定状态下,
    // SecItemCopyMatching 读取凭据会永远挂起,刷新管道在 proc.run() 之前就被
    // 堵死,600s 超时 killer 根本没机会布防,整个 App 因此停摆三天。所以:
    // 1) 查询放到独立串行队列,等待有硬超时,超时就不带凭据继续刷新;
    // 2) 卡住的查询只允许存在一个,期间后续调用直接返回空;
    // 3) 60s TTL 缓存把健康路径的查询降到偶发。
    private static let overridesCacheLock = NSLock()
    private static var overridesCache: (value: [String: String], at: Date)?
    private static let overridesCacheTTL: TimeInterval = 60
    private static let overridesLookupQueue = DispatchQueue(
        label: "com.tokei.app.provider-overrides", qos: .utility)
    private static let overridesLookupGate = NSLock()
    private static var overridesLookupInFlight = false
    private static let overridesLookupTimeout: TimeInterval = 5

    private static func cachedOverrides() -> [String: String]? {
        overridesCacheLock.lock()
        defer { overridesCacheLock.unlock() }
        guard let cache = overridesCache,
              Date().timeIntervalSince(cache.at) < overridesCacheTTL else { return nil }
        return cache.value
    }

    private static func storeOverridesCache(_ value: [String: String]) {
        overridesCacheLock.lock()
        overridesCache = (value, Date())
        overridesCacheLock.unlock()
    }

    private static func invalidateOverridesCache() {
        overridesCacheLock.lock()
        overridesCache = nil
        overridesCacheLock.unlock()
    }

    /// 禁用的提供方不得读取或导出其已保存的凭据（上游 c8b10a8 的门禁，
    /// 合并时对 sub2api/zai/minimax 三支被 -X ours 覆盖丢失，此处补回）。
    private static func collectOverrides() -> [String: String] {
        var result: [String: String] = [:]
        if providerQuotaEnabled("sub2api"), let token = token(for: .sub2api) {
            result["SUB2API_API_KEY"] = token
        }
        if providerQuotaEnabled("zai"), let token = token(for: .zai) {
            result["Z_AI_API_KEY"] = token
        }
        if providerQuotaEnabled("minimax"), let token = token(for: .minimax) {
            result["TOKEI_MINIMAX_API_KEY"] = token
        }
        if providerQuotaEnabled("zed"), let credentials = zedCredentials() {
            result["TOKEI_ZED_USER_ID"] = credentials.userID
            result["TOKEI_ZED_ACCESS_TOKEN"] = credentials.accessToken
        }
        return result
    }

    static func environmentOverrides() -> [String: String] {
        // TOKEI_*_QUOTA 环境变量存在时（测试/显式覆盖），同步计算且不经缓存，
        // 保证启用状态翻转立即生效；生产路径走异步 + TTL 缓存，避免反复触碰钥匙串。
        let environment = ProcessInfo.processInfo.environment
        let hasEnvOverride = ["sub2api", "zai", "minimax", "zed"].contains {
            environment["TOKEI_\($0.uppercased())_QUOTA"] != nil
        }
        if hasEnvOverride {
            return collectOverrides()
        }
        if let cached = cachedOverrides() { return cached }
        overridesLookupGate.lock()
        let alreadyStuck = overridesLookupInFlight
        overridesLookupInFlight = true
        overridesLookupGate.unlock()
        if alreadyStuck {
            // 上一轮查询还卡在 Keychain 里:这次不带凭据继续,别跟着堵死。
            return [:]
        }
        let semaphore = DispatchSemaphore(value: 0)
        overridesLookupQueue.async {
            defer {
                overridesLookupGate.lock()
                overridesLookupInFlight = false
                overridesLookupGate.unlock()
            }
            let result = collectOverrides()
            storeOverridesCache(result)
            semaphore.signal()
        }
        let waited = semaphore.wait(timeout: .now() + overridesLookupTimeout)
        if waited != .success {
            logKeychainStall(
                "provider keychain lookup exceeded \(Int(overridesLookupTimeout))s — continuing without credential overrides (provider quotas will go stale until the keychain responds)"
            )
        }
        return cachedOverrides() ?? [:]
    }

    /// 与 DataLoader.logScriptFailure 同一文件、同一滚动上限，但保持本模块零依赖——
    /// 测试（test_provider_credentials）单独编译本文件，不允许引用其他源文件。
    private static func logKeychainStall(_ message: String) {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".tokei/script_error.log")
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        let line = "[\(formatter.string(from: Date()))] \(message)\n"
        if let existing = try? Data(contentsOf: url), existing.count > 256 * 1024 {
            try? existing.suffix(128 * 1024).write(to: url, options: .atomic)
        }
        if let handle = try? FileHandle(forWritingTo: url) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: Data(line.utf8))
        } else {
            try? FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? Data(line.utf8).write(to: url, options: .atomic)
        }
    }

    private static func baseQuery(
        for provider: ProviderSecret,
        service: String
    ) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: provider.rawValue,
        ]
#if TOKEI_PROVIDER_CREDENTIAL_STORE_TEST
        query[kSecMatchSearchList as String] = [testKeychain]
#endif
        return query
    }

    private static func targetTestKeychainForAdd(_ item: inout [String: Any]) {
#if TOKEI_PROVIDER_CREDENTIAL_STORE_TEST
        item.removeValue(forKey: kSecMatchSearchList as String)
        item[kSecUseKeychain as String] = testKeychain
#endif
    }

    private static func providerQuotaEnabled(_ provider: String) -> Bool {
        let envKey = "TOKEI_\(provider.uppercased())_QUOTA"
        if ProcessInfo.processInfo.environment[envKey] == "1" { return true }
        if ProcessInfo.processInfo.environment[envKey] == "0" { return false }
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".tokei/config.json")
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return false }
        return object["\(provider)_quota_enabled"] as? Bool ?? false
    }

    private struct ZedSettings: Decodable {
        var credentialsURL: String?
        var serverURL: String?

        enum CodingKeys: String, CodingKey {
            case credentialsURL = "credentials_url"
            case serverURL = "server_url"
        }
    }

    private static func zedCredentials(
        allowInteraction: Bool = false
    ) -> (userID: String, accessToken: String)? {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/zed/settings.json")
        let settings = (try? Data(contentsOf: url))
            .flatMap { try? JSONDecoder().decode(ZedSettings.self, from: $0) }
        let credentialsURL = settings?.credentialsURL?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let serverURL = settings?.serverURL?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let serviceURL = [credentialsURL, serverURL, "https://zed.dev"]
            .compactMap { value -> String? in
                guard let value, !value.isEmpty else { return nil }
                return value
            }
            .first ?? "https://zed.dev"

        if let credentials = queryZedCredentials(
            itemClass: kSecClassInternetPassword,
            attribute: kSecAttrServer,
            value: serviceURL,
            allowInteraction: allowInteraction
        ) {
            return credentials
        }
        return queryZedCredentials(
            itemClass: kSecClassGenericPassword,
            attribute: kSecAttrService,
            value: serviceURL,
            allowInteraction: allowInteraction
        )
    }

    private static func queryZedCredentials(
        itemClass: CFTypeRef,
        attribute: CFString,
        value: String,
        allowInteraction: Bool
    ) -> (userID: String, accessToken: String)? {
        var query: [String: Any] = [
            kSecClass as String: itemClass,
            attribute as String: value,
            kSecMatchLimit as String: kSecMatchLimitOne,
            kSecReturnAttributes as String: true,
            kSecReturnData as String: true,
        ]
        if !allowInteraction {
            applyNoUI(to: &query)
        }
        var result: AnyObject?
        let status: OSStatus
        if allowInteraction {
            status = SecItemCopyMatching(query as CFDictionary, &result)
        } else {
            status = withoutKeychainUI {
                SecItemCopyMatching(query as CFDictionary, &result)
            }
        }
        guard status == errSecSuccess,
              let item = result as? [String: Any],
              let userID = item[kSecAttrAccount as String] as? String,
              let data = item[kSecValueData as String] as? Data,
              let accessToken = String(data: data, encoding: .utf8),
              !userID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !accessToken.isEmpty else { return nil }
        return (userID, accessToken)
    }

    private static func withoutKeychainUI(_ body: () -> OSStatus) -> OSStatus {
        interactionLock.lock()
        defer { interactionLock.unlock() }
        var interactionAllowed: DarwinBoolean = false
        guard SecKeychainGetUserInteractionAllowed(&interactionAllowed) == errSecSuccess,
              SecKeychainSetUserInteractionAllowed(false) == errSecSuccess else {
            return errSecInteractionNotAllowed
        }
        defer {
            _ = SecKeychainSetUserInteractionAllowed(interactionAllowed.boolValue)
        }
        return body()
    }

    private static let uiFailPolicy: String = {
        let path = "/System/Library/Frameworks/Security.framework/Security"
        guard let handle = dlopen(path, RTLD_NOW) else { return "u_AuthUIF" }
        defer { dlclose(handle) }
        guard let symbol = dlsym(handle, "kSecUseAuthenticationUIFail") else {
            return "u_AuthUIF"
        }
        return symbol.assumingMemoryBound(to: CFString?.self).pointee as String? ?? "u_AuthUIF"
    }()

    private static func applyNoUI(to query: inout [String: Any]) {
        let context = LAContext()
        context.interactionNotAllowed = true
        query[kSecUseAuthenticationContext as String] = context
        query[kSecUseAuthenticationUI as String] = uiFailPolicy as CFString
    }
}
