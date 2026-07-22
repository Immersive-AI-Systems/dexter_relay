import CoreGraphics
import Foundation

enum TouchLifecycleState: String, Codable {
    case began
    case moved
    case stationary
    case ended
    case cancelled
}

enum TouchRole: String, Codable, CaseIterable {
    case left
    case right

    var sortOrder: Int {
        self == .left ? 0 : 1
    }
}

struct TrackedTouchSample: Codable {
    let id: Int
    let role: TouchRole
    /// Target-relative horizontal offset in centimeters; positive is right.
    let x: Double
    /// Target-relative vertical offset in centimeters; positive is up.
    let y: Double
    let normalizedX: Double
    let normalizedY: Double
    let active: Bool
    let state: TouchLifecycleState
}

struct TouchViewSize: Codable {
    let width: Double
    let height: Double

    init(_ size: CGSize) {
        width = Double(size.width)
        height = Double(size.height)
    }
}

struct TouchCalibration: Codable {
    let pointsPerCentimeter: Double
    let targetSeparationCm: Double
}

/// An event captured on the main thread before transport metadata is added.
struct TouchFrame {
    let timestamp: TimeInterval
    let monotonicTime: TimeInterval
    let event: TouchLifecycleState
    let touches: [TrackedTouchSample]
    let view: TouchViewSize
    let orientation: String
    let calibration: TouchCalibration
}

struct TouchPacket: Encodable {
    let protocolName = "ipad-dexter-touch"
    let version = 2
    let coordinateSystem = "target-offset-centimeters"
    let sessionId: String
    let sequence: UInt64
    let timestamp: TimeInterval
    let monotonicTime: TimeInterval
    let event: TouchLifecycleState
    let touches: [TrackedTouchSample]
    let view: TouchViewSize
    let orientation: String
    let calibration: TouchCalibration

    enum CodingKeys: String, CodingKey {
        case protocolName = "protocol"
        case version
        case coordinateSystem
        case sessionId
        case sequence
        case timestamp
        case monotonicTime
        case event
        case touches
        case view
        case orientation
        case calibration
    }

    init(sessionId: String, sequence: UInt64, frame: TouchFrame) {
        self.sessionId = sessionId
        self.sequence = sequence
        timestamp = frame.timestamp
        monotonicTime = frame.monotonicTime
        event = frame.event
        touches = frame.touches
        view = frame.view
        orientation = frame.orientation
        calibration = frame.calibration
    }
}
