import Foundation
import Network

enum UDPTransmitterConfigurationError: LocalizedError {
    case emptyHost
    case invalidPort

    var errorDescription: String? {
        switch self {
        case .emptyHost:
            return "Enter the relay machine's IP address or host name."
        case .invalidPort:
            return "Port must be a number between 1 and 65535."
        }
    }
}

final class UDPTransmitter {
    enum Status: Equatable {
        case stopped
        case starting(String, UInt16)
        case ready(String, UInt16)
        case waiting(String)
        case failed(String)

        var text: String {
            switch self {
            case .stopped:
                return "Not sending"
            case let .starting(host, port):
                return "Starting \(host):\(port)…"
            case let .ready(host, port):
                return "Sending UDP to \(host):\(port)"
            case let .waiting(message):
                return "Network waiting: \(message)"
            case let .failed(message):
                return "Network error: \(message)"
            }
        }
    }

    var onStatusChange: ((Status) -> Void)?

    private let queue = DispatchQueue(label: "com.example.DexterTouch.udp")
    private let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }()

    private var connection: NWConnection?
    private var sequence: UInt64 = 0
    private var sessionId = UUID().uuidString

    deinit {
        connection?.cancel()
    }

    func start(host rawHost: String, port rawPort: String) throws {
        let host = rawHost.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !host.isEmpty else {
            throw UDPTransmitterConfigurationError.emptyHost
        }
        guard let port = UInt16(rawPort), port > 0 else {
            throw UDPTransmitterConfigurationError.invalidPort
        }

        publish(.starting(host, port))
        queue.async { [weak self] in
            guard let self else { return }
            self.stopLocked(publishStatus: false)
            self.sequence = 0
            self.sessionId = UUID().uuidString
            let connection = NWConnection(
                host: NWEndpoint.Host(host),
                port: NWEndpoint.Port(rawValue: port)!,
                using: .udp
            )
            self.connection = connection
            connection.stateUpdateHandler = { [weak self, weak connection] state in
                guard let self, let connection, self.connection === connection else { return }
                self.handle(state: state, host: host, port: port)
            }
            connection.start(queue: self.queue)
        }
    }

    func stop() {
        queue.async { [weak self] in
            self?.stopLocked(publishStatus: true)
        }
    }

    func send(frame: TouchFrame) {
        queue.async { [weak self] in
            guard let self, let connection = self.connection else { return }

            self.sequence &+= 1
            let packet = TouchPacket(
                sessionId: self.sessionId,
                sequence: self.sequence,
                frame: frame
            )

            do {
                let data = try self.encoder.encode(packet)
                connection.send(content: data, completion: .contentProcessed { [weak self] error in
                    if let error {
                        self?.publish(.failed("Send failed: \(error.localizedDescription)"))
                    }
                })
            } catch {
                self.publish(.failed("Encoding failed: \(error.localizedDescription)"))
            }
        }
    }

    private func handle(state: NWConnection.State, host: String, port: UInt16) {
        switch state {
        case .setup, .preparing:
            publish(.starting(host, port))
        case .ready:
            publish(.ready(host, port))
        case let .waiting(error):
            publish(.waiting(error.localizedDescription))
        case let .failed(error):
            publish(.failed(error.localizedDescription))
            stopLocked(publishStatus: false)
        case .cancelled:
            if connection == nil {
                publish(.stopped)
            }
        @unknown default:
            publish(.failed("Unknown Network framework state"))
        }
    }

    private func stopLocked(publishStatus: Bool) {
        let oldConnection = connection
        connection = nil
        oldConnection?.stateUpdateHandler = nil
        oldConnection?.cancel()
        if publishStatus {
            publish(.stopped)
        }
    }

    private func publish(_ status: Status) {
        DispatchQueue.main.async { [weak self] in
            self?.onStatusChange?(status)
        }
    }
}
