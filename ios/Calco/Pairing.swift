import Foundation
import Security

/// The computer this phone is paired with: network code (its address) and password.
/// The code is the computer's IPv4 address in Crockford base 32, e.g. "30AG-0EK" (engine/calco/pairing.py).
enum Pairing {
    private static let alphabet = Array("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    private static let codeKey = "pairingCode"
    private static let account = "calco.pairing.password"

    /// "30AG-0EK" -> "192.168.1.211"; nil when the code is not valid.
    static func address(fromCode code: String) -> String? {
        let cleaned = code.uppercased()
            .replacingOccurrences(of: "-", with: "").replacingOccurrences(of: " ", with: "")
            .replacingOccurrences(of: "I", with: "1").replacingOccurrences(of: "L", with: "1")
            .replacingOccurrences(of: "O", with: "0")
        guard cleaned.count == 7 else { return nil }
        var n: UInt64 = 0
        for ch in cleaned {
            guard let v = alphabet.firstIndex(of: ch) else { return nil }
            n = n * 32 + UInt64(v)
        }
        guard n < (1 << 32) else { return nil }
        return [24, 16, 8, 0].map { String((n >> UInt64($0)) & 255) }.joined(separator: ".")
    }

    static func format(_ code: String) -> String {
        let c = code.uppercased().filter { $0 != "-" && $0 != " " }
        return c.count > 4 ? "\(c.prefix(4))-\(c.dropFirst(4))" : c
    }

    static var code: String {
        get { UserDefaults.standard.string(forKey: codeKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: codeKey) }
    }

    /// Stored in the Keychain, not in the app's files.
    static var password: String {
        get {
            let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account,
                                        kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
            var item: CFTypeRef?
            guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return "" }
            return String(decoding: data, as: UTF8.self)
        }
        set {
            let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account]
            SecItemDelete(base as CFDictionary)
            guard !newValue.isEmpty else { return }
            var add = base
            add[kSecValueData as String] = Data(newValue.utf8)
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    static var isPaired: Bool { address(fromCode: code) != nil && password.count == 6 }

    static func check(_ given: String?) -> Bool {
        guard let given, isPaired else { return false }
        return given == password
    }
}
