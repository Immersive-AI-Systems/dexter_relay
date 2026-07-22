import Foundation

enum AppConfiguration {
    static let defaultHost = "10.40.49.105"
    static let defaultPort: UInt16 = 5005
    /// 264 physical pixels/inch at the common iPad 2x display scale.
    static let defaultPointsPerCentimeter = 51.9685

    static let hostDefaultsKey = "receiverHost"
    static let portDefaultsKey = "receiverPort"
    static let pointsPerCentimeterDefaultsKey = "pointsPerCentimeter"

    private static let legacyDefaultHost = "192.168.1.100"
    private static let hostDefaultVersionKey = "receiverHostDefaultVersion"
    private static let currentHostDefaultVersion = 1

    static var savedHost: String {
        let defaults = UserDefaults.standard
        if defaults.integer(forKey: hostDefaultVersionKey) < currentHostDefaultVersion {
            let saved = defaults.string(forKey: hostDefaultsKey)
            if saved == nil || saved == legacyDefaultHost {
                defaults.set(defaultHost, forKey: hostDefaultsKey)
            }
            defaults.set(currentHostDefaultVersion, forKey: hostDefaultVersionKey)
        }
        return defaults.string(forKey: hostDefaultsKey) ?? defaultHost
    }

    static var savedPort: UInt16 {
        let saved = UserDefaults.standard.integer(forKey: portDefaultsKey)
        guard saved > 0, saved <= Int(UInt16.max) else {
            return defaultPort
        }
        return UInt16(saved)
    }

    static var savedPointsPerCentimeter: Double {
        let saved = UserDefaults.standard.double(forKey: pointsPerCentimeterDefaultsKey)
        guard (44.0...68.0).contains(saved) else {
            return defaultPointsPerCentimeter
        }
        return saved
    }

    static func save(host: String, port: UInt16) {
        UserDefaults.standard.set(host, forKey: hostDefaultsKey)
        UserDefaults.standard.set(Int(port), forKey: portDefaultsKey)
    }

    static func save(pointsPerCentimeter: Double) {
        UserDefaults.standard.set(pointsPerCentimeter, forKey: pointsPerCentimeterDefaultsKey)
    }
}
