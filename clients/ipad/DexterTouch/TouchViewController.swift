import UIKit

final class TouchViewController: UIViewController, UITextFieldDelegate {
    private let transmitter = UDPTransmitter()
    private let canvas = TouchCanvasView()
    private let hostField = UITextField()
    private let portField = UITextField()
    private let sendButton = UIButton(type: .system)
    private let calibrationButton = UIButton(type: .system)
    private let statusLabel = UILabel()
    private var sendingRequested = false

    override func viewDidLoad() {
        super.viewDidLoad()
        configureUI()
        configureCallbacks()

        NotificationCenter.default.addObserver(
            self,
            selector: #selector(appWillResignActive),
            name: UIApplication.willResignActiveNotification,
            object: nil
        )
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
        transmitter.stop()
    }

    private func configureUI() {
        view.backgroundColor = .systemBackground
        canvas.pointsPerCentimeter = CGFloat(AppConfiguration.savedPointsPerCentimeter)

        let titleLabel = UILabel()
        titleLabel.text = "Dexter Touch"
        titleLabel.font = .systemFont(ofSize: 25, weight: .bold)

        configureTextField(hostField, placeholder: "Receiver host")
        hostField.text = AppConfiguration.savedHost
        hostField.keyboardType = .numbersAndPunctuation
        hostField.textContentType = .URL
        hostField.returnKeyType = .next
        hostField.accessibilityLabel = "Receiver host"

        configureTextField(portField, placeholder: "Port")
        portField.text = String(AppConfiguration.savedPort)
        portField.keyboardType = .numberPad
        portField.accessibilityLabel = "Receiver port"

        var buttonConfiguration = UIButton.Configuration.filled()
        buttonConfiguration.title = "Start Sending"
        buttonConfiguration.cornerStyle = .medium
        sendButton.configuration = buttonConfiguration
        sendButton.addTarget(self, action: #selector(toggleSending), for: .touchUpInside)

        var calibrationConfiguration = UIButton.Configuration.tinted()
        calibrationConfiguration.cornerStyle = .medium
        calibrationButton.configuration = calibrationConfiguration
        updateCalibrationButtonTitle()
        calibrationButton.addTarget(self, action: #selector(showCalibration), for: .touchUpInside)

        statusLabel.text = UDPTransmitter.Status.stopped.text
        statusLabel.font = .systemFont(ofSize: 14, weight: .medium)
        statusLabel.textColor = .secondaryLabel
        statusLabel.numberOfLines = 2

        let hostLabel = fieldLabel("Relay host")
        let portLabel = fieldLabel("UDP port")
        let hostStack = UIStackView(arrangedSubviews: [hostLabel, hostField])
        hostStack.axis = .vertical
        hostStack.spacing = 4
        let portStack = UIStackView(arrangedSubviews: [portLabel, portField])
        portStack.axis = .vertical
        portStack.spacing = 4

        let controls = UIStackView(arrangedSubviews: [hostStack, portStack, calibrationButton, sendButton])
        controls.axis = .horizontal
        controls.alignment = .bottom
        controls.spacing = 12

        let header = UIStackView(arrangedSubviews: [titleLabel, controls, statusLabel])
        header.axis = .vertical
        header.spacing = 9
        header.translatesAutoresizingMaskIntoConstraints = false

        let headerBackground = UIView()
        headerBackground.backgroundColor = .secondarySystemBackground
        headerBackground.translatesAutoresizingMaskIntoConstraints = false
        headerBackground.addSubview(header)

        canvas.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(headerBackground)
        view.addSubview(canvas)

        NSLayoutConstraint.activate([
            headerBackground.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            headerBackground.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            headerBackground.trailingAnchor.constraint(equalTo: view.trailingAnchor),

            header.topAnchor.constraint(equalTo: headerBackground.topAnchor, constant: 12),
            header.leadingAnchor.constraint(equalTo: headerBackground.leadingAnchor, constant: 18),
            header.trailingAnchor.constraint(equalTo: headerBackground.trailingAnchor, constant: -18),
            header.bottomAnchor.constraint(equalTo: headerBackground.bottomAnchor, constant: -12),

            hostField.heightAnchor.constraint(equalToConstant: 40),
            hostStack.widthAnchor.constraint(greaterThanOrEqualToConstant: 210),
            portField.heightAnchor.constraint(equalToConstant: 40),
            portStack.widthAnchor.constraint(equalToConstant: 110),
            calibrationButton.heightAnchor.constraint(equalToConstant: 40),
            calibrationButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 150),
            sendButton.heightAnchor.constraint(equalToConstant: 40),
            sendButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 145),

            canvas.topAnchor.constraint(equalTo: headerBackground.bottomAnchor),
            canvas.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            canvas.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            canvas.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
    }

    private func configureCallbacks() {
        canvas.onFrame = { [weak self] frame in
            self?.transmitter.send(frame: frame)
        }
        transmitter.onStatusChange = { [weak self] status in
            self?.apply(status: status)
        }
    }

    private func configureTextField(_ field: UITextField, placeholder: String) {
        field.placeholder = placeholder
        field.borderStyle = .roundedRect
        field.autocapitalizationType = .none
        field.autocorrectionType = .no
        field.spellCheckingType = .no
        field.clearButtonMode = .whileEditing
        field.delegate = self
    }

    private func fieldLabel(_ text: String) -> UILabel {
        let label = UILabel()
        label.text = text
        label.font = .systemFont(ofSize: 12, weight: .semibold)
        label.textColor = .secondaryLabel
        return label
    }

    @objc private func toggleSending() {
        view.endEditing(true)
        if sendingRequested {
            transmitter.stop()
            return
        }

        do {
            let host = hostField.text ?? ""
            let portText = portField.text ?? ""
            try transmitter.start(host: host, port: portText)
            guard let port = UInt16(portText), port > 0 else { return }
            AppConfiguration.save(
                host: host.trimmingCharacters(in: .whitespacesAndNewlines),
                port: port
            )
            sendingRequested = true
            setConfigurationEditingEnabled(false)
            setButtonTitle("Stop Sending")
        } catch {
            statusLabel.text = error.localizedDescription
            statusLabel.textColor = .systemRed
        }
    }

    @objc private func showCalibration() {
        canvas.cancelAllTouches()
        let controller = CalibrationViewController(pointsPerCentimeter: canvas.pointsPerCentimeter)
        controller.onUseCalibration = { [weak self] pointsPerCentimeter in
            guard let self else { return }
            self.canvas.pointsPerCentimeter = pointsPerCentimeter
            AppConfiguration.save(pointsPerCentimeter: Double(pointsPerCentimeter))
            self.updateCalibrationButtonTitle()
        }
        present(controller, animated: true)
    }

    private func updateCalibrationButtonTitle() {
        calibrationButton.configuration?.title = String(
            format: "Calibrate %.1f pt/cm",
            canvas.pointsPerCentimeter
        )
    }

    private func apply(status: UDPTransmitter.Status) {
        statusLabel.text = status.text
        switch status {
        case .ready:
            statusLabel.textColor = .systemGreen
        case .starting, .waiting:
            statusLabel.textColor = .systemOrange
        case .failed:
            statusLabel.textColor = .systemRed
            sendingRequested = false
            setConfigurationEditingEnabled(true)
            setButtonTitle("Start Sending")
        case .stopped:
            statusLabel.textColor = .secondaryLabel
            sendingRequested = false
            setConfigurationEditingEnabled(true)
            setButtonTitle("Start Sending")
        }
    }

    private func setConfigurationEditingEnabled(_ enabled: Bool) {
        hostField.isEnabled = enabled
        portField.isEnabled = enabled
        calibrationButton.isEnabled = enabled
    }

    private func setButtonTitle(_ title: String) {
        sendButton.configuration?.title = title
    }

    @objc private func appWillResignActive() {
        canvas.cancelAllTouches()
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool {
        if textField === hostField {
            portField.becomeFirstResponder()
        } else {
            textField.resignFirstResponder()
        }
        return true
    }
}
