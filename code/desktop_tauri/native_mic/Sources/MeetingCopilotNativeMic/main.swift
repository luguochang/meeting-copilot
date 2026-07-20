import AVFoundation
import Darwin
import Foundation

private let nativePCMProtocolName = "native_pcm_v2"
private let nativePCMMagic = Data([0x4d, 0x43, 0x50, 0x43, 0x4d, 0x32, 0x00, 0x00])

struct Configuration {
    let webSocketURL: URL
    let cookie: String
    let sessionID: String
    let readyFile: URL
    let durationSeconds: Double?
    let captureEpoch: UInt64
}

struct ProbeConfiguration {
    let durationSeconds: Double
}

enum NativeMicError: Error, CustomStringConvertible {
    case usage(String)
    case permissionDenied
    case invalidInputFormat
    case invalidOutputFormat
    case websocket(String)
    case audio(String)

    var description: String {
        switch self {
        case .usage(let message): return message
        case .permissionDenied: return "microphone permission was denied"
        case .invalidInputFormat: return "microphone input format is unavailable"
        case .invalidOutputFormat: return "16 kHz mono output format could not be created"
        case .websocket(let message): return "websocket error: \(message)"
        case .audio(let message): return "audio error: \(message)"
        }
    }
}

extension Configuration {
    static let usage = """
    Usage:
      meeting-copilot-native-mic --ws-url URL --session-id ID --ready-file PATH [--duration SECONDS]
      meeting-copilot-native-mic --probe --duration SECONDS
    """

    static func parse(_ arguments: [String]) throws -> Configuration {
        var webSocketURL: URL?
        let cookie = ProcessInfo.processInfo.environment["MEETING_COPILOT_SESSION_COOKIE"] ?? ""
        var sessionID: String?
        var readyFile: URL?
        var durationSeconds: Double?
        var index = 0

        func value(after flag: String) throws -> String {
            guard index + 1 < arguments.count else {
                throw NativeMicError.usage("missing value after \(flag)\n\n\(usage)")
            }
            index += 1
            return arguments[index]
        }

        while index < arguments.count {
            switch arguments[index] {
            case "--ws-url":
                guard let url = URL(string: try value(after: "--ws-url")),
                      url.scheme == "ws" || url.scheme == "wss" else {
                    throw NativeMicError.usage("--ws-url must be a ws:// or wss:// URL")
                }
                webSocketURL = url
            case "--session-id":
                let value = try value(after: "--session-id")
                guard !value.isEmpty,
                      value.count <= 128,
                      value.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-" || $0 == ".") }) else {
                    throw NativeMicError.usage("--session-id contains unsafe characters")
                }
                sessionID = value
            case "--ready-file":
                readyFile = URL(fileURLWithPath: try value(after: "--ready-file"), isDirectory: false)
            case "--duration":
                let value = try value(after: "--duration")
                guard let seconds = Double(value), seconds > 0, seconds <= 3600 else {
                    throw NativeMicError.usage("--duration must be between 0 and 3600 seconds")
                }
                durationSeconds = seconds
            case "--help", "-h":
                throw NativeMicError.usage(usage)
            default:
                throw NativeMicError.usage("unknown argument: \(arguments[index])\n\n\(usage)")
            }
            index += 1
        }

        guard let webSocketURL, let sessionID, let readyFile else {
            throw NativeMicError.usage("--ws-url, --session-id and --ready-file are required\n\n\(usage)")
        }
        let captureEpoch = try nativeCaptureEpoch(webSocketURL)
        return Configuration(
            webSocketURL: webSocketURL,
            cookie: cookie,
            sessionID: sessionID,
            readyFile: readyFile,
            durationSeconds: durationSeconds,
            captureEpoch: captureEpoch
        )
    }
}

private func nativeCaptureEpoch(_ url: URL) throws -> UInt64 {
    guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
          components.scheme == "ws",
          components.host == "127.0.0.1",
          components.port != nil,
          components.path.hasPrefix("/live/asr/stream/ws/") else {
        throw NativeMicError.usage("--ws-url must use the authenticated ws://127.0.0.1 backend")
    }
    let values = Dictionary(
        grouping: components.queryItems ?? [],
        by: \.name
    ).mapValues { items in items.compactMap(\.value) }
    guard values["pcm_protocol"] == [nativePCMProtocolName],
          let epochValues = values["capture_epoch"],
          epochValues.count == 1,
          let epochValue = epochValues.first,
          let epoch = UInt64(epochValue),
          epoch > 0 else {
        throw NativeMicError.usage(
            "--ws-url must declare pcm_protocol=native_pcm_v2 and a positive capture_epoch"
        )
    }
    return epoch
}

private func appendBigEndian<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
    var encoded = value.bigEndian
    withUnsafeBytes(of: &encoded) { bytes in
        data.append(contentsOf: bytes)
    }
}

private func nativePCMEnvelope(
    pcm: Data,
    captureEpoch: UInt64,
    sequence: UInt64,
    timestampMilliseconds: UInt64,
    finalPartial: Bool
) throws -> Data {
    let frameBytes = 4_800 * MemoryLayout<Float>.size
    guard !pcm.isEmpty,
          pcm.count <= frameBytes,
          pcm.count.isMultiple(of: MemoryLayout<Float>.size),
          sequence > 0 else {
        throw NativeMicError.invalidOutputFormat
    }
    var envelope = Data(capacity: 44 + pcm.count)
    envelope.append(nativePCMMagic)
    envelope.append(2)
    envelope.append(1)
    appendBigEndian(UInt16(finalPartial ? 1 : 0), to: &envelope)
    appendBigEndian(captureEpoch, to: &envelope)
    appendBigEndian(sequence, to: &envelope)
    appendBigEndian(timestampMilliseconds, to: &envelope)
    appendBigEndian(UInt32(16_000), to: &envelope)
    appendBigEndian(UInt32(pcm.count), to: &envelope)
    envelope.append(pcm)
    return envelope
}

extension ProbeConfiguration {
    static func parse(_ arguments: [String]) throws -> ProbeConfiguration {
        guard arguments.count == 3,
              arguments[0] == "--probe",
              arguments[1] == "--duration",
              let duration = Double(arguments[2]),
              duration >= 2,
              duration <= 3 else {
            throw NativeMicError.usage("--probe requires --duration between 2 and 3 seconds\n\n\(Configuration.usage)")
        }
        return ProbeConfiguration(durationSeconds: duration)
    }
}

private func requestMicrophonePermission() async throws {
    let status = AVCaptureDevice.authorizationStatus(for: .audio)
    if status == .authorized { return }
    if status == .denied || status == .restricted {
        throw NativeMicError.permissionDenied
    }
    let granted = await withCheckedContinuation { continuation in
        AVCaptureDevice.requestAccess(for: .audio) { value in
            continuation.resume(returning: value)
        }
    }
    guard granted else { throw NativeMicError.permissionDenied }
}

final class NativeMicrophoneProbe {
    private static let audibleRMSThreshold = 0.002
    private let configuration: ProbeConfiguration
    private let engine = AVAudioEngine()
    private let stateLock = NSLock()
    private var startedAtNanos: UInt64 = 0
    private var sumSquares = 0.0
    private var sampleCount: UInt64 = 0
    private var peakRMS = 0.0
    private var running = false

    init(configuration: ProbeConfiguration) {
        self.configuration = configuration
    }

    func start() async throws {
        try await requestMicrophonePermission()
        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0,
              inputFormat.channelCount > 0,
              inputFormat.commonFormat == .pcmFormatFloat32 else {
            throw NativeMicError.invalidInputFormat
        }
        inputNode.installTap(onBus: 0, bufferSize: 1_024, format: inputFormat) { [weak self] buffer, _ in
            self?.accumulate(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            inputNode.removeTap(onBus: 0)
            throw NativeMicError.audio(String(describing: error))
        }
        markStarted()
    }

    func finish() -> Bool {
        stopEngine()
        stateLock.lock()
        let startedAt = startedAtNanos
        let squares = sumSquares
        let count = sampleCount
        let peak = peakRMS
        stateLock.unlock()
        guard startedAt > 0, count > 0 else {
            FileHandle.standardError.write(Data("fatal: microphone input returned no samples\n".utf8))
            return false
        }
        let elapsedNanos = DispatchTime.now().uptimeNanoseconds - startedAt
        let durationMilliseconds = UInt64((Double(elapsedNanos) / 1_000_000.0).rounded())
        let rms = sqrt(squares / Double(count))
        let payload: [String: Any] = [
            "schema_version": "meeting_copilot.native_mic_probe.v1",
            "probe_status": peak >= Self.audibleRMSThreshold ? "audible" : "silent",
            "sampled": true,
            "rms": max(0, min(1, rms)),
            "peak_rms": max(0, min(1, peak)),
            "duration_ms": durationMilliseconds,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else {
            FileHandle.standardError.write(Data("fatal: microphone probe result could not be encoded\n".utf8))
            return false
        }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        return true
    }

    func stop() {
        stopEngine()
    }

    private func accumulate(_ buffer: AVAudioPCMBuffer) {
        guard let channels = buffer.floatChannelData else { return }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return }
        var bufferSquares = 0.0
        for channelIndex in 0..<channelCount {
            let channel = channels[channelIndex]
            for frameIndex in 0..<frames {
                let sample = Double(channel[frameIndex])
                bufferSquares += sample * sample
            }
        }
        let bufferSamples = UInt64(frames * channelCount)
        let bufferRMS = sqrt(bufferSquares / Double(bufferSamples))
        stateLock.lock()
        sumSquares += bufferSquares
        sampleCount += bufferSamples
        peakRMS = max(peakRMS, bufferRMS)
        stateLock.unlock()
    }

    private func markStarted() {
        stateLock.lock()
        startedAtNanos = DispatchTime.now().uptimeNanoseconds
        running = true
        stateLock.unlock()
    }

    private func stopEngine() {
        stateLock.lock()
        let shouldStop = running
        running = false
        stateLock.unlock()
        guard shouldStop else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }
}

final class WebSocketDelegate: NSObject, URLSessionWebSocketDelegate {
    let opened = DispatchSemaphore(value: 0)
    private(set) var openError: Error?

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        opened.signal()
    }

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        if closeCode != .normalClosure && closeCode != .goingAway {
            openError = NativeMicError.websocket("closed with code \(closeCode.rawValue)")
        }
    }
}

final class NativeMicrophoneStreamer {
    private static let audibleRMSThreshold: Float = 0.002
    private static let frameSamples = 4_800
    private static let frameBytes = frameSamples * MemoryLayout<Float>.size
    private let configuration: Configuration
    private let engine = AVAudioEngine()
    private let sendQueue = DispatchQueue(label: "com.meetingcopilot.native-mic.send")
    private let stateLock = NSLock()
    private var stopped = false
    private var stopping = false
    private var paused = false
    private var pendingPCM = Data()
    private var outputConverter: AVAudioConverter?
    private var webSocketSession: URLSession?
    private var webSocketTask: URLSessionWebSocketTask?
    private var pingTimer: DispatchSourceTimer?
    private var lastLevelEmissionNanos: UInt64 = 0
    private var pcmSequence: UInt64 = 0
    private var lastPCMTimestampMilliseconds: UInt64 = 0
    private var transportReady = false
    private var pcmSeen = false
    private var audiblePCMSeen = false
    private var firstPCMRMS: Float?
    private var pcmBytesSent: UInt64 = 0
    private var transportFailed = false

    init(configuration: Configuration) {
        self.configuration = configuration
    }

    func start() async throws {
        writeDiagnostic("stage=request_permission")
        try await requestPermissionIfNeeded()
        writeDiagnostic("stage=permission_granted")

        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        writeDiagnostic("stage=input_format sample_rate=\(inputFormat.sampleRate) channels=\(inputFormat.channelCount)")
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw NativeMicError.invalidInputFormat
        }
        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 16_000,
            channels: 1,
            interleaved: false
        ) else {
            throw NativeMicError.invalidOutputFormat
        }
        guard let converter = AVAudioConverter(from: inputFormat, to: outputFormat) else {
            throw NativeMicError.invalidOutputFormat
        }
        outputConverter = converter

        writeDiagnostic("stage=websocket_connecting")
        try connectWebSocket()
        writeDiagnostic("stage=websocket_open")
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.handleAudio(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            inputNode.removeTap(onBus: 0)
            throw NativeMicError.audio(String(describing: error))
        }
        writeDiagnostic("stage=audio_engine_started")
        try await waitForTransportedPCMAndWriteReady()
        writeDiagnostic("stage=transported_pcm_ready")
    }

    func stop() {
        stateLock.lock()
        if stopped || stopping {
            stateLock.unlock()
            return
        }
        stopping = true
        stateLock.unlock()

        pingTimer?.cancel()
        pingTimer = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        sendQueue.sync {
            guard let task = webSocketTask else { return }
            if !pendingPCM.isEmpty {
                pcmSequence += 1
                do {
                    try sendBinary(
                        pendingPCM,
                        sequence: pcmSequence,
                        timestampMilliseconds: lastPCMTimestampMilliseconds,
                        finalPartial: true,
                        task: task
                    )
                } catch {
                    writeDiagnostic("stage=websocket_final_flush_failed error=\(error)")
                }
                pendingPCM.removeAll(keepingCapacity: false)
            }
            let semaphore = DispatchSemaphore(value: 0)
            task.send(.string("END")) { _ in semaphore.signal() }
            _ = semaphore.wait(timeout: .now() + 2.0)
        }
        stateLock.lock()
        stopped = true
        stopping = false
        stateLock.unlock()
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketSession?.invalidateAndCancel()
    }

    func togglePause(_ shouldPause: Bool) {
        stateLock.lock()
        paused = shouldPause
        stateLock.unlock()
    }

    private func requestPermissionIfNeeded() async throws {
        try await requestMicrophonePermission()
    }

    private func connectWebSocket() throws {
        let delegate = WebSocketDelegate()
        let sessionConfiguration = URLSessionConfiguration.ephemeral
        sessionConfiguration.timeoutIntervalForRequest = 120
        sessionConfiguration.timeoutIntervalForResource = 24 * 60 * 60
        let session = URLSession(configuration: sessionConfiguration, delegate: delegate, delegateQueue: nil)
        var request = URLRequest(url: configuration.webSocketURL)
        if !configuration.cookie.isEmpty {
            request.setValue(configuration.cookie, forHTTPHeaderField: "Cookie")
        }
        let task = session.webSocketTask(with: request)
        webSocketSession = session
        webSocketTask = task
        task.resume()
        guard delegate.opened.wait(timeout: .now() + 15.0) == .success else {
            task.cancel(with: .goingAway, reason: nil)
            throw NativeMicError.websocket("connection timed out")
        }
        recordTransportReady()
        receiveLoop(task)
        startHeartbeat(task)
    }

    private func startHeartbeat(_ task: URLSessionWebSocketTask) {
        let timer = DispatchSource.makeTimerSource(queue: sendQueue)
        timer.schedule(deadline: .now() + 15.0, repeating: 15.0)
        timer.setEventHandler { [weak self, weak task] in
            guard let self, let task, !self.isInactive() else { return }
            task.sendPing { [weak self] error in
                guard let error, let self, !self.isInactive() else { return }
                FileHandle.standardError.write(Data(("websocket ping failed: \(error)\n").utf8))
            }
        }
        pingTimer = timer
        timer.resume()
    }

    private func receiveLoop(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                if case .string(let text) = message {
                    FileHandle.standardOutput.write(Data((text + "\n").utf8))
                }
                self.receiveLoop(task)
            case .failure(let error):
                if !self.isInactive() {
                    self.failTransport(NativeMicError.websocket("receive failed: \(error)"))
                }
            }
        }
    }

    private func handleAudio(_ buffer: AVAudioPCMBuffer) {
        stateLock.lock()
        let shouldDrop = stopped || stopping || paused
        stateLock.unlock()
        if shouldDrop { return }

        guard let converter = outputConverter,
              let outputFormat = converter.outputFormat as AVAudioFormat?,
              let output = AVAudioPCMBuffer(
                  pcmFormat: outputFormat,
                  frameCapacity: AVAudioFrameCount(Double(buffer.frameLength) * 16_000.0 / buffer.format.sampleRate) + 64
              ) else { return }
        var conversionError: NSError?
        var supplied = false
        let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
            if supplied {
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, conversionError == nil, output.frameLength > 0,
              let channel = output.floatChannelData?[0] else { return }
        let bytes = Data(bytes: channel, count: Int(output.frameLength) * MemoryLayout<Float>.size)
        var sumSquares: Float = 0
        for index in 0..<Int(output.frameLength) {
            let sample = channel[index]
            sumSquares += sample * sample
        }
        let rms = output.frameLength > 0
            ? sqrt(sumSquares / Float(output.frameLength))
            : 0
        let level = max(0, min(1, rms * 6))
        sendQueue.async { [weak self] in
            guard let self, let task = self.webSocketTask else { return }
            let now = DispatchTime.now().uptimeNanoseconds
            if now - self.lastLevelEmissionNanos >= 100_000_000 {
                self.lastLevelEmissionNanos = now
                self.writeEvent([
                    "event_type": "input_level",
                    "level": level,
                    "at_ms": Int(Date().timeIntervalSince1970 * 1_000),
                ])
            }
            self.pendingPCM.append(bytes)
            self.lastPCMTimestampMilliseconds = now / 1_000_000
            while self.pendingPCM.count >= Self.frameBytes {
                let frame = Data(self.pendingPCM.prefix(Self.frameBytes))
                self.pendingPCM.removeFirst(Self.frameBytes)
                self.pcmSequence += 1
                do {
                    try self.sendBinary(
                        frame,
                        sequence: self.pcmSequence,
                        timestampMilliseconds: self.lastPCMTimestampMilliseconds,
                        finalPartial: false,
                        task: task
                    )
                    self.recordSuccessfulPCMSend(
                        byteCount: frame.count,
                        rms: self.rms(of: frame)
                    )
                } catch {
                    self.failTransport(error)
                    return
                }
            }
        }
    }

    private func sendBinary(
        _ data: Data,
        sequence: UInt64,
        timestampMilliseconds: UInt64,
        finalPartial: Bool,
        task: URLSessionWebSocketTask
    ) throws {
        let envelope = try nativePCMEnvelope(
            pcm: data,
            captureEpoch: configuration.captureEpoch,
            sequence: sequence,
            timestampMilliseconds: timestampMilliseconds,
            finalPartial: finalPartial
        )
        let semaphore = DispatchSemaphore(value: 0)
        var sendError: Error?
        task.send(.data(envelope)) { error in
            sendError = error
            semaphore.signal()
        }
        let completed = semaphore.wait(timeout: .now() + 5.0) == .success
        if !completed {
            throw NativeMicError.websocket("binary PCM send timed out")
        } else if let sendError {
            throw NativeMicError.websocket("binary PCM send failed: \(sendError)")
        }
    }

    private func failTransport(_ error: Error) {
        stateLock.lock()
        let shouldFail = !stopped && !stopping && !transportFailed
        transportFailed = true
        stateLock.unlock()
        guard shouldFail else { return }
        writeDiagnostic("stage=websocket_transport_failed error=\(error)")
        removeReadyFile()
        DispatchQueue.main.async { [weak self] in
            self?.stop()
            exit(70)
        }
    }

    private func waitForTransportedPCMAndWriteReady() async throws {
        let deadline = Date().addingTimeInterval(15)
        while true {
            let startup = startupSnapshot()
            if startup.transportFailed {
                throw NativeMicError.websocket("transport failed before readiness was committed")
            }
            if startup.transportReady && startup.pcmSeen {
                try writeReady(startup)
                if markReadyWritten() { return }
                removeReadyFile()
                throw NativeMicError.audio("microphone stopped before readiness was committed")
            }
            if isInactive() {
                throw NativeMicError.audio("microphone stopped before transporting PCM")
            }
            if Date() >= deadline {
                throw NativeMicError.audio("microphone produced no complete transported PCM frame")
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
    }

    private struct StartupSnapshot {
        let transportReady: Bool
        let pcmSeen: Bool
        let audiblePCMSeen: Bool
        let firstPCMRMS: Float?
        let pcmBytesSent: UInt64
        let transportFailed: Bool
    }

    private func writeReady(_ startup: StartupSnapshot) throws {
        let payload: [String: Any] = [
            "schema_version": "meeting_copilot.native_mic_ready.v1",
            "status": "ready",
            "session_id": configuration.sessionID,
            "sample_rate_hz": 16_000,
            "channels": 1,
            "sample_format": "pcm_f32le",
            "frame_samples": Self.frameSamples,
            "source": "av_audio_engine_microphone",
            "transport_ready": startup.transportReady,
            "pcm_seen": startup.pcmSeen,
            "audible_pcm_seen": startup.audiblePCMSeen,
            "first_pcm_rms": startup.firstPCMRMS ?? NSNull(),
            "pcm_bytes_sent": startup.pcmBytesSent,
            "pcm_protocol": nativePCMProtocolName,
            "capture_epoch": configuration.captureEpoch,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else {
            throw NativeMicError.audio("ready payload could not be encoded")
        }
        do {
            try FileManager.default.createDirectory(
                at: configuration.readyFile.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try data.write(to: configuration.readyFile, options: .atomic)
        } catch {
            throw NativeMicError.audio("ready file could not be written: \(error)")
        }
    }

    private func recordTransportReady() {
        stateLock.lock()
        transportReady = true
        stateLock.unlock()
    }

    private func recordSuccessfulPCMSend(byteCount: Int, rms: Float) {
        stateLock.lock()
        if !pcmSeen { firstPCMRMS = rms }
        pcmSeen = true
        audiblePCMSeen = audiblePCMSeen || rms >= Self.audibleRMSThreshold
        let (updatedBytes, overflow) = pcmBytesSent.addingReportingOverflow(UInt64(byteCount))
        pcmBytesSent = overflow ? UInt64.max : updatedBytes
        stateLock.unlock()
    }

    private func startupSnapshot() -> StartupSnapshot {
        stateLock.lock()
        defer { stateLock.unlock() }
        return StartupSnapshot(
            transportReady: transportReady,
            pcmSeen: pcmSeen,
            audiblePCMSeen: audiblePCMSeen,
            firstPCMRMS: firstPCMRMS,
            pcmBytesSent: pcmBytesSent,
            transportFailed: transportFailed
        )
    }

    private func markReadyWritten() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        guard !stopped, !stopping, !transportFailed else { return false }
        return true
    }

    private func removeReadyFile() {
        try? FileManager.default.removeItem(at: configuration.readyFile)
    }

    private func rms(of pcm: Data) -> Float {
        guard !pcm.isEmpty else { return 0 }
        let sumSquares = pcm.withUnsafeBytes { rawBuffer -> Double in
            let samples = rawBuffer.bindMemory(to: Float.self)
            return samples.reduce(into: 0.0) { sum, sample in
                let finiteSample = sample.isFinite ? Double(sample) : 0
                sum += finiteSample * finiteSample
            }
        }
        let sampleCount = pcm.count / MemoryLayout<Float>.size
        return Float(min(1, sqrt(sumSquares / Double(sampleCount))))
    }

    private func writeDiagnostic(_ message: String) {
        FileHandle.standardError.write(Data(("native_mic: \(message)\n").utf8))
    }

    private func writeEvent(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else { return }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }

    private func isInactive() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return stopped || stopping
    }
}

@main
struct MeetingCopilotNativeMicMain {
    static func main() {
        do {
            let arguments = Array(CommandLine.arguments.dropFirst())
            if arguments.first == "--probe" {
                let configuration = try ProbeConfiguration.parse(arguments)
                let probe = NativeMicrophoneProbe(configuration: configuration)
                signal(SIGTERM, SIG_IGN)
                let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
                termination.setEventHandler {
                    probe.stop()
                    exit(0)
                }
                termination.resume()
                Task {
                    do {
                        try await probe.start()
                        DispatchQueue.main.asyncAfter(deadline: .now() + configuration.durationSeconds) {
                            exit(probe.finish() ? 0 : 70)
                        }
                    } catch {
                        FileHandle.standardError.write(Data(("fatal: \(error)\n").utf8))
                        exit(70)
                    }
                }
                dispatchMain()
            }
            let configuration = try Configuration.parse(arguments)
            let streamer = NativeMicrophoneStreamer(configuration: configuration)
            let parentPID = getppid()
            signal(SIGTERM, SIG_IGN)
            signal(SIGUSR1, SIG_IGN)
            signal(SIGUSR2, SIG_IGN)
            let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
            termination.setEventHandler {
                streamer.stop()
                exit(0)
            }
            termination.resume()
            let pause = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
            pause.setEventHandler { streamer.togglePause(true) }
            pause.resume()
            let resume = DispatchSource.makeSignalSource(signal: SIGUSR2, queue: .main)
            resume.setEventHandler { streamer.togglePause(false) }
            resume.resume()

            let parentMonitor = DispatchSource.makeTimerSource(queue: .main)
            parentMonitor.schedule(deadline: .now() + 1.0, repeating: 1.0)
            parentMonitor.setEventHandler {
                if getppid() != parentPID || kill(parentPID, 0) != 0 {
                    FileHandle.standardError.write(Data("native_mic: stage=parent_lost\n".utf8))
                    streamer.stop()
                    exit(0)
                }
            }
            parentMonitor.resume()

            Task {
                do {
                    try await streamer.start()
                    if let duration = configuration.durationSeconds {
                        DispatchQueue.main.asyncAfter(deadline: .now() + duration) {
                            FileHandle.standardError.write(Data("native_mic: stage=duration_reached\n".utf8))
                            streamer.stop()
                            exit(0)
                        }
                    }
                } catch {
                    FileHandle.standardError.write(Data(("fatal: \(error)\n").utf8))
                    exit(70)
                }
            }
            // The synchronous process entry owns libdispatch's main loop. Calling
            // dispatchMain() after an async main resumes on the main queue traps.
            dispatchMain()
        } catch NativeMicError.usage(let message) {
            FileHandle.standardError.write(Data((message + "\n").utf8))
            exit(message == Configuration.usage ? 0 : 64)
        } catch {
            FileHandle.standardError.write(Data(("fatal: \(error)\n").utf8))
            exit(70)
        }
    }
}
