import UIKit

final class TouchCanvasView: UIView {
    static let targetSeparationCm = 3.0
    static let readoutVerticalOffsetCm = 10.0

    var onFrame: ((TouchFrame) -> Void)?
    var pointsPerCentimeter = CGFloat(AppConfiguration.defaultPointsPerCentimeter) {
        didSet {
            contentScaleFactor = UIScreen.main.scale
            setNeedsDisplay()
        }
    }

    private struct TouchRecord {
        let id: Int
        let role: TouchRole
        let touch: UITouch
    }

    private var records: [ObjectIdentifier: TouchRecord] = [:]
    private var nextTouchId = 1

    override init(frame: CGRect) {
        super.init(frame: frame)
        configure()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        configure()
    }

    private func configure() {
        isMultipleTouchEnabled = true
        isExclusiveTouch = true
        contentMode = .redraw
        backgroundColor = UIColor(red: 0.055, green: 0.071, blue: 0.102, alpha: 1)
        accessibilityLabel = "Calibrated left and right finger tracking surface"
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        var changed = Set<ObjectIdentifier>()
        let candidates = touches.filter {
            $0.type == .direct && records[ObjectIdentifier($0)] == nil
        }
        let availableRoles = TouchRole.allCases.filter { role in
            !records.values.contains(where: { $0.role == role })
        }

        if availableRoles.count == 2, candidates.count >= 2 {
            let horizontal = candidates.sorted {
                $0.location(in: self).x < $1.location(in: self).x
            }
            register(horizontal.first!, as: .left, changed: &changed)
            register(horizontal.last!, as: .right, changed: &changed)
        } else {
            for touch in candidates {
                guard records.count < 2 else { break }
                let remaining = TouchRole.allCases.filter { role in
                    !records.values.contains(where: { $0.role == role })
                }
                guard !remaining.isEmpty else { break }
                let role = remaining.count == 1 ? remaining[0] : nearestRole(to: touch)
                register(touch, as: role, changed: &changed)
            }
        }

        guard !changed.isEmpty else { return }
        emit(event: .began, changed: changed)
        setNeedsDisplay()
    }

    private func register(
        _ touch: UITouch,
        as role: TouchRole,
        changed: inout Set<ObjectIdentifier>
    ) {
        let key = ObjectIdentifier(touch)
        guard records[key] == nil else { return }
        records[key] = TouchRecord(id: nextTouchId, role: role, touch: touch)
        nextTouchId += 1
        changed.insert(key)
    }

    private func nearestRole(to touch: UITouch) -> TouchRole {
        let point = touch.location(in: self)
        let leftDistance = squaredDistance(point, targetCenter(for: .left))
        let rightDistance = squaredDistance(point, targetCenter(for: .right))
        return leftDistance <= rightDistance ? .left : .right
    }

    private func squaredDistance(_ first: CGPoint, _ second: CGPoint) -> CGFloat {
        let dx = first.x - second.x
        let dy = first.y - second.y
        return dx * dx + dy * dy
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        let changed = trackedKeys(in: touches)
        guard !changed.isEmpty else { return }
        emit(event: .moved, changed: changed)
        setNeedsDisplay()
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        finish(touches: touches, state: .ended)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        finish(touches: touches, state: .cancelled)
    }

    /// Called when iPadOS interrupts the app so receivers never retain stale active touches.
    func cancelAllTouches() {
        let changed = Set(records.keys)
        guard !changed.isEmpty else { return }
        emit(event: .cancelled, changed: changed)
        records.removeAll()
        setNeedsDisplay()
    }

    private func finish(touches: Set<UITouch>, state: TouchLifecycleState) {
        let changed = trackedKeys(in: touches)
        guard !changed.isEmpty else { return }
        emit(event: state, changed: changed)
        for key in changed {
            records.removeValue(forKey: key)
        }
        setNeedsDisplay()
    }

    private func trackedKeys(in touches: Set<UITouch>) -> Set<ObjectIdentifier> {
        Set(touches.compactMap { touch in
            let key = ObjectIdentifier(touch)
            return records[key] == nil ? nil : key
        })
    }

    private func emit(event: TouchLifecycleState, changed: Set<ObjectIdentifier>) {
        guard bounds.width > 0, bounds.height > 0 else { return }

        let samples = records.map { key, record -> TrackedTouchSample in
            let point = record.touch.location(in: self)
            let target = targetCenter(for: record.role)
            let state: TouchLifecycleState = changed.contains(key) ? event : .stationary
            return TrackedTouchSample(
                id: record.id,
                role: record.role,
                x: centimeters(point.x - target.x),
                y: centimeters(target.y - point.y),
                normalizedX: normalized(point.x, maximum: bounds.width),
                normalizedY: normalized(point.y, maximum: bounds.height),
                active: state != .ended && state != .cancelled,
                state: state
            )
        }
        .sorted { $0.role.sortOrder < $1.role.sortOrder }

        onFrame?(
            TouchFrame(
                timestamp: Date().timeIntervalSince1970,
                monotonicTime: ProcessInfo.processInfo.systemUptime,
                event: event,
                touches: samples,
                view: TouchViewSize(bounds.size),
                orientation: interfaceOrientationName,
                calibration: TouchCalibration(
                    pointsPerCentimeter: Double(pointsPerCentimeter),
                    targetSeparationCm: Self.targetSeparationCm
                )
            )
        )
    }

    private func centimeters(_ points: CGFloat) -> Double {
        let value = Double(points / pointsPerCentimeter)
        return abs(value) < 0.000_5 ? 0 : value
    }

    private func normalized(_ value: CGFloat, maximum: CGFloat) -> Double {
        Double(min(max(value / maximum, 0), 1))
    }

    private var interfaceOrientationName: String {
        switch window?.windowScene?.interfaceOrientation {
        case .portrait:
            return "portrait"
        case .portraitUpsideDown:
            return "portraitUpsideDown"
        case .landscapeLeft:
            return "landscapeLeft"
        case .landscapeRight:
            return "landscapeRight"
        default:
            return "unknown"
        }
    }

    override func draw(_ rect: CGRect) {
        super.draw(rect)
        drawGrid(in: rect)
        drawReadoutPanel(in: rect)
        drawTargets()
        drawInstruction()

        for record in records.values.sorted(by: { $0.role.sortOrder < $1.role.sortOrder }) {
            let point = record.touch.location(in: self)
            let color = color(for: record.role)
            let radius: CGFloat = 34
            color.withAlphaComponent(0.22).setFill()
            UIBezierPath(ovalIn: CGRect(
                x: point.x - radius,
                y: point.y - radius,
                width: radius * 2,
                height: radius * 2
            )).fill()

            color.setStroke()
            let ring = UIBezierPath(arcCenter: point, radius: radius, startAngle: 0, endAngle: .pi * 2, clockwise: true)
            ring.lineWidth = 3
            ring.stroke()

            let label = "\(record.role.rawValue.uppercased()) #\(record.id)" as NSString
            let attributes: [NSAttributedString.Key: Any] = [
                .font: UIFont.monospacedDigitSystemFont(ofSize: 17, weight: .bold),
                .foregroundColor: UIColor.white
            ]
            let size = label.size(withAttributes: attributes)
            label.draw(
                at: CGPoint(x: point.x - size.width / 2, y: point.y - size.height / 2),
                withAttributes: attributes
            )
        }
    }

    private func drawGrid(in rect: CGRect) {
        let path = UIBezierPath()
        let spacing = pointsPerCentimeter
        var x: CGFloat = 0
        while x <= rect.width {
            path.move(to: CGPoint(x: x, y: 0))
            path.addLine(to: CGPoint(x: x, y: rect.height))
            x += spacing
        }
        var y: CGFloat = 0
        while y <= rect.height {
            path.move(to: CGPoint(x: 0, y: y))
            path.addLine(to: CGPoint(x: rect.width, y: y))
            y += spacing
        }
        UIColor.white.withAlphaComponent(0.045).setStroke()
        path.lineWidth = 1
        path.stroke()
    }

    private func targetCenter(for role: TouchRole) -> CGPoint {
        let halfSeparation = CGFloat(Self.targetSeparationCm) * pointsPerCentimeter / 2
        let x = bounds.midX + (role == .left ? -halfSeparation : halfSeparation)
        let y = bounds.midY
        return CGPoint(x: x, y: y)
    }

    private func color(for role: TouchRole) -> UIColor {
        role == .left ? .systemCyan : .systemOrange
    }

    private func drawTargets() {
        for role in TouchRole.allCases {
            let center = targetCenter(for: role)
            let halfLength = 0.36 * pointsPerCentimeter
            let path = UIBezierPath()
            path.move(to: CGPoint(x: center.x - halfLength, y: center.y - halfLength))
            path.addLine(to: CGPoint(x: center.x + halfLength, y: center.y + halfLength))
            path.move(to: CGPoint(x: center.x + halfLength, y: center.y - halfLength))
            path.addLine(to: CGPoint(x: center.x - halfLength, y: center.y + halfLength))
            color(for: role).setStroke()
            path.lineWidth = 5
            path.lineCapStyle = .round
            path.stroke()

            drawCentered(
                role.rawValue.uppercased(),
                at: CGPoint(x: center.x, y: center.y + 0.62 * pointsPerCentimeter),
                font: .systemFont(ofSize: 13, weight: .bold),
                color: color(for: role)
            )
        }
    }

    private func drawInstruction() {
        let targetsY = targetCenter(for: .left).y
        drawCentered(
            "Place each finger on its X  •  +x right  •  +y up",
            at: CGPoint(x: bounds.midX, y: targetsY - 1.35 * pointsPerCentimeter),
            font: .systemFont(ofSize: 16, weight: .medium),
            color: UIColor.white.withAlphaComponent(0.62)
        )
    }

    private func drawReadoutPanel(in rect: CGRect) {
        let panelHeight: CGFloat = 94
        let exactCenterY = targetCenter(for: .left).y
            - CGFloat(Self.readoutVerticalOffsetCm) * pointsPerCentimeter
        let centerY = max(panelHeight / 2 + 10, exactCenterY)
        let panelWidth = min(rect.width - 32, 560)
        let panelRect = CGRect(
            x: rect.midX - panelWidth / 2,
            y: centerY - panelHeight / 2,
            width: panelWidth,
            height: panelHeight
        )

        UIColor.black.withAlphaComponent(0.38).setFill()
        UIBezierPath(roundedRect: panelRect, cornerRadius: 16).fill()

        let divider = UIBezierPath()
        divider.move(to: CGPoint(x: panelRect.midX, y: panelRect.minY + 12))
        divider.addLine(to: CGPoint(x: panelRect.midX, y: panelRect.maxY - 12))
        UIColor.white.withAlphaComponent(0.14).setStroke()
        divider.lineWidth = 1
        divider.stroke()

        let columnWidth = panelRect.width / 2
        drawReadout(
            for: .left,
            in: CGRect(x: panelRect.minX, y: panelRect.minY, width: columnWidth, height: panelHeight)
        )
        drawReadout(
            for: .right,
            in: CGRect(x: panelRect.midX, y: panelRect.minY, width: columnWidth, height: panelHeight)
        )
    }

    private func drawReadout(for role: TouchRole, in rect: CGRect) {
        drawCentered(
            "\(role.rawValue.uppercased()) FINGER",
            at: CGPoint(x: rect.midX, y: rect.minY + 23),
            font: .systemFont(ofSize: 14, weight: .bold),
            color: color(for: role)
        )

        let record = records.values.first(where: { $0.role == role })
        let value: String
        if let record {
            let point = record.touch.location(in: self)
            let target = targetCenter(for: role)
            value = String(
                format: "x %+.2f cm   y %+.2f cm",
                centimeters(point.x - target.x),
                centimeters(target.y - point.y)
            )
        } else {
            value = "x  --  cm   y  --  cm"
        }
        drawCentered(
            value,
            at: CGPoint(x: rect.midX, y: rect.minY + 61),
            font: .monospacedDigitSystemFont(ofSize: 18, weight: .semibold),
            color: .white
        )
    }

    private func drawCentered(_ text: String, at point: CGPoint, font: UIFont, color: UIColor) {
        let label = text as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: color
        ]
        let size = label.size(withAttributes: attributes)
        label.draw(
            at: CGPoint(x: point.x - size.width / 2, y: point.y - size.height / 2),
            withAttributes: attributes
        )
    }
}
