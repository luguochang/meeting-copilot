import AVFoundation
import Foundation

struct Configuration {
    let webSocketURL: URL
    let cookie: String
    let sessionID: String
    let readyFile: URL
    let durationSeconds: Double?
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
        return Configuration(
            webSocketURL: webSocketURL,
            cookie: cookie,
            sessionID: sessionID,
            readyFile: readyFile,
            durationSeconds: durationSeconds
        )
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

    init(configuration: Configuration) {
        self.configuration = configuration
    }

    func start() throws {
        try requestPermissionIfNeeded()

        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
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

        try connectWebSocket()
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
        writeReady()
    }

    func stop() {
        stateLock.lock()
        if stopped || stopping {
            stateLock.unlock()
            return
        }
        stopping = true
        stateLock.unlock()

        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        sendQueue.sync {
            guard let task = webSocketTask else { return }
            if !pendingPCM.isEmpty {
                sendBinary(pendingPCM, task: task)
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

    private func requestPermissionIfNeeded() throws {
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        if status == .authorized { return }
        if status == .denied || status == .restricted {
            throw NativeMicError.permissionDenied
        }
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .audio) { value in
            granted = value
            semaphore.signal()
        }
        _ = semaphore.wait(timeout: .now() + 30.0)
        guard granted else { throw NativeMicError.permissionDenied }
    }

    private func connectWebSocket() throws {
        let delegate = WebSocketDelegate()
        let session = URLSession(configuration: .default, delegate: delegate, delegateQueue: nil)
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
        receiveLoop(task)
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
                if !self.isStopped() {
                    FileHandle.standardError.write(Data(("websocket receive failed: \(error)\n").utf8))
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
        sendQueue.async { [weak self] in
            guard let self, let task = self.webSocketTask else { return }
            self.pendingPCM.append(bytes)
            while self.pendingPCM.count >= Self.frameBytes {
                let frame = Data(self.pendingPCM.prefix(Self.frameBytes))
                self.pendingPCM.removeFirst(Self.frameBytes)
                self.sendBinary(frame, task: task)
            }
        }
    }

    private func sendBinary(_ data: Data, task: URLSessionWebSocketTask) {
        let semaphore = DispatchSemaphore(value: 0)
        var sendError: Error?
        task.send(.data(data)) { error in
            sendError = error
            semaphore.signal()
        }
        let completed = semaphore.wait(timeout: .now() + 5.0) == .success
        if !completed, !isStopped() {
            FileHandle.standardError.write(Data("websocket send timed out\n".utf8))
        } else if let sendError, !isStopped() {
            FileHandle.standardError.write(Data(("websocket send failed: \(sendError)\n").utf8))
        }
    }

    private func writeReady() {
        let payload: [String: Any] = [
            "schema_version": "meeting_copilot.native_mic_ready.v1",
            "status": "ready",
            "session_id": configuration.sessionID,
            "sample_rate_hz": 16_000,
            "channels": 1,
            "sample_format": "pcm_f32le",
            "frame_samples": Self.frameSamples,
            "source": "av_audio_engine_microphone",
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else { return }
        do {
            try FileManager.default.createDirectory(
                at: configuration.readyFile.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try data.write(to: configuration.readyFile, options: .atomic)
        } catch {
            FileHandle.standardError.write(Data(("failed to write ready file: \(error)\n").utf8))
        }
    }

    private func isStopped() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return stopped
    }
}

@main
struct MeetingCopilotNativeMicMain {
    static func main() async {
        do {
            let configuration = try Configuration.parse(Array(CommandLine.arguments.dropFirst()))
            let streamer = NativeMicrophoneStreamer(configuration: configuration)
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

            try streamer.start()
            if let duration = configuration.durationSeconds {
                DispatchQueue.main.asyncAfter(deadline: .now() + duration) {
                    streamer.stop()
                    exit(0)
                }
            }
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
