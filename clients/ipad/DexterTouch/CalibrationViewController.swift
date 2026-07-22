import UIKit

final class CalibrationViewController: UIViewController {
    var onUseCalibration: ((CGFloat) -> Void)?

    private let rulerView = CalibrationRulerView()
    private let slider = UISlider()
    private let valueLabel = UILabel()
    private let initialPointsPerCentimeter: CGFloat

    init(pointsPerCentimeter: CGFloat) {
        initialPointsPerCentimeter = pointsPerCentimeter
        super.init(nibName: nil, bundle: nil)
        modalPresentationStyle = .fullScreen
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        let titleLabel = UILabel()
        titleLabel.text = "Calibrate physical centimeters"
        titleLabel.font = .systemFont(ofSize: 30, weight: .bold)
        titleLabel.textAlignment = .center

        let explanationLabel = UILabel()
        explanationLabel.text = "Place a physical ruler against the line. Adjust the slider until the distance from 0 to 10 is exactly 10 cm."
        explanationLabel.font = .systemFont(ofSize: 18)
        explanationLabel.textColor = .secondaryLabel
        explanationLabel.textAlignment = .center
        explanationLabel.numberOfLines = 0

        rulerView.pointsPerCentimeter = initialPointsPerCentimeter
        rulerView.translatesAutoresizingMaskIntoConstraints = false

        slider.minimumValue = 44
        slider.maximumValue = 68
        slider.value = Float(initialPointsPerCentimeter)
        slider.addTarget(self, action: #selector(sliderChanged), for: .valueChanged)

        valueLabel.font = .monospacedDigitSystemFont(ofSize: 17, weight: .semibold)
        valueLabel.textAlignment = .center
        updateValueLabel()

        var cancelConfiguration = UIButton.Configuration.tinted()
        cancelConfiguration.title = "Cancel"
        let cancelButton = UIButton(configuration: cancelConfiguration)
        cancelButton.addTarget(self, action: #selector(cancelCalibration), for: .touchUpInside)

        var doneConfiguration = UIButton.Configuration.filled()
        doneConfiguration.title = "Use Calibration"
        let doneButton = UIButton(configuration: doneConfiguration)
        doneButton.addTarget(self, action: #selector(useCalibration), for: .touchUpInside)

        let buttons = UIStackView(arrangedSubviews: [cancelButton, doneButton])
        buttons.axis = .horizontal
        buttons.distribution = .fillEqually
        buttons.spacing = 16

        let stack = UIStackView(arrangedSubviews: [titleLabel, explanationLabel, rulerView, valueLabel, slider, buttons])
        stack.axis = .vertical
        stack.spacing = 22
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -24),
            stack.centerYAnchor.constraint(equalTo: view.safeAreaLayoutGuide.centerYAnchor),
            rulerView.heightAnchor.constraint(equalToConstant: 190),
            slider.heightAnchor.constraint(equalToConstant: 44),
            buttons.heightAnchor.constraint(equalToConstant: 50)
        ])
    }

    @objc private func sliderChanged() {
        rulerView.pointsPerCentimeter = CGFloat(slider.value)
        updateValueLabel()
    }

    private func updateValueLabel() {
        valueLabel.text = String(format: "%.2f UIKit points per centimeter", slider.value)
    }

    @objc private func cancelCalibration() {
        dismiss(animated: true)
    }

    @objc private func useCalibration() {
        onUseCalibration?(CGFloat(slider.value))
        dismiss(animated: true)
    }
}

private final class CalibrationRulerView: UIView {
    var pointsPerCentimeter: CGFloat = CGFloat(AppConfiguration.defaultPointsPerCentimeter) {
        didSet { setNeedsDisplay() }
    }

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .secondarySystemBackground
        layer.cornerRadius = 16
        contentMode = .redraw
        accessibilityLabel = "Adjustable ten centimeter calibration line"
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func draw(_ rect: CGRect) {
        super.draw(rect)
        let length = pointsPerCentimeter * 10
        let start = CGPoint(x: rect.midX - length / 2, y: rect.midY)
        let end = CGPoint(x: rect.midX + length / 2, y: rect.midY)
        let path = UIBezierPath()
        path.move(to: start)
        path.addLine(to: end)

        for centimeter in 0...10 {
            let x = start.x + CGFloat(centimeter) * pointsPerCentimeter
            let halfTick: CGFloat = centimeter == 0 || centimeter == 10 ? 18 : 10
            path.move(to: CGPoint(x: x, y: rect.midY - halfTick))
            path.addLine(to: CGPoint(x: x, y: rect.midY + halfTick))
        }

        UIColor.label.setStroke()
        path.lineWidth = 3
        path.stroke()

        drawLabel("0", centeredAt: CGPoint(x: start.x, y: rect.midY + 34))
        drawLabel("10 cm", centeredAt: CGPoint(x: end.x, y: rect.midY + 34))
    }

    private func drawLabel(_ text: String, centeredAt point: CGPoint) {
        let label = text as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: UIFont.monospacedDigitSystemFont(ofSize: 17, weight: .semibold),
            .foregroundColor: UIColor.label
        ]
        let size = label.size(withAttributes: attributes)
        label.draw(
            at: CGPoint(x: point.x - size.width / 2, y: point.y - size.height / 2),
            withAttributes: attributes
        )
    }
}
