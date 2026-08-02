import Foundation
import Security

enum NinebotKeychainError: LocalizedError {
    case unexpectedStatus(OSStatus)
    case invalidData

    var errorDescription: String? {
        switch self {
        case .unexpectedStatus(let status):
            return "Keychain 操作失败（\(status)）"
        case .invalidData:
            return "Keychain 中的凭据格式无效"
        }
    }
}

protocol NinebotSecureStoring: Sendable {
    func string(for account: String) throws -> String?
    func save(_ value: String, for account: String) throws
    func removeValue(for account: String) throws
}

struct NinebotKeychain: NinebotSecureStoring, Sendable {
    private let service: String

    init(service: String = "com.example.NineBotPlus.credentials") {
        self.service = service
    }

    func string(for account: String) throws -> String? {
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        switch status {
        case errSecSuccess:
            guard let data = result as? Data,
                  let value = String(data: data, encoding: .utf8) else {
                throw NinebotKeychainError.invalidData
            }
            return normalized(value)
        case errSecItemNotFound:
            return nil
        default:
            throw NinebotKeychainError.unexpectedStatus(status)
        }
    }

    func save(_ value: String, for account: String) throws {
        guard let cleaned = normalized(value) else {
            try removeValue(for: account)
            return
        }

        let data = Data(cleaned.utf8)
        let query = baseQuery(account: account)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw NinebotKeychainError.unexpectedStatus(updateStatus)
        }

        var addQuery = query
        attributes.forEach { addQuery[$0.key] = $0.value }
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw NinebotKeychainError.unexpectedStatus(addStatus)
        }
    }

    func removeValue(for account: String) throws {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NinebotKeychainError.unexpectedStatus(status)
        }
    }

    private func baseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    private func normalized(_ value: String?) -> String? {
        let cleaned = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return cleaned.isEmpty ? nil : cleaned
    }
}

struct NinebotCredentials: Equatable, Sendable {
    var apiKey: String?
    var sessionToken: String?
}

struct NinebotCredentialStore: Sendable {
    static let shared = NinebotCredentialStore()

    private enum Account {
        static let apiKey = "nineplus.api-key"
        static let sessionToken = "nineplus.session-token"
    }

    private let storage: any NinebotSecureStoring

    init(storage: any NinebotSecureStoring = NinebotKeychain()) {
        self.storage = storage
    }

    func loadAPIKey() throws -> String? {
        try storage.string(for: Account.apiKey)
    }

    func saveAPIKey(_ value: String?) throws {
        try save(value, account: Account.apiKey)
    }

    func loadSessionToken() throws -> String? {
        try storage.string(for: Account.sessionToken)
    }

    func saveSessionToken(_ value: String?) throws {
        try save(value, account: Account.sessionToken)
    }

    func resolvedConfiguration(from configuration: NinebotServerConfiguration) throws -> NinebotServerConfiguration {
        var resolved = configuration
        resolved.bearerToken = try loadAPIKey() ?? ""
        resolved.appSessionToken = try loadSessionToken()
        return resolved
    }

    func migrateLegacyCredentials(
        apiKey legacyAPIKey: String?,
        sessionToken legacySessionToken: String?
    ) throws -> NinebotCredentials {
        var apiKey = try loadAPIKey()
        if apiKey == nil, let legacyAPIKey = normalized(legacyAPIKey) {
            try saveAPIKey(legacyAPIKey)
            apiKey = try loadAPIKey()
        }

        var sessionToken = try loadSessionToken()
        if sessionToken == nil, let legacySessionToken = normalized(legacySessionToken) {
            try saveSessionToken(legacySessionToken)
            sessionToken = try loadSessionToken()
        }

        return NinebotCredentials(apiKey: apiKey, sessionToken: sessionToken)
    }

    func removeAll() throws {
        var firstError: Error?
        do {
            try storage.removeValue(for: Account.apiKey)
        } catch {
            firstError = error
        }
        do {
            try storage.removeValue(for: Account.sessionToken)
        } catch {
            if firstError == nil {
                firstError = error
            }
        }
        if let firstError {
            throw firstError
        }
    }

    private func save(_ value: String?, account: String) throws {
        if let value = normalized(value) {
            try storage.save(value, for: account)
        } else {
            try storage.removeValue(for: account)
        }
    }

    private func normalized(_ value: String?) -> String? {
        let cleaned = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return cleaned.isEmpty ? nil : cleaned
    }
}
