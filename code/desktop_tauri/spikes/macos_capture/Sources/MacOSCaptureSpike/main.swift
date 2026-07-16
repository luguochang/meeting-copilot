import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

enum CaptureMode: String, Codable {
    case probe
    case mic
    case system
    case both

    var wantsMicrophone: Bool { self == .mic || self == .both }
    var wantsSystemAudio: Bool { self == .system || self == .both }
}

enum SpikeError: Error, CustomStringConvertible {
    case usage(String)
    case permission(String)
    case capture(String)

    var description: String {
        switch self {
        case .usage(let message), .permission(let message), .capture(let message):
            return message
        }
    }
}

struct Configuration {
    let mode: CaptureMode
    let durationSeconds: Double
    let outputDirectory: URL
    let evidenceURL: URL
    let requestPermissions: Bool
    let displayID: CGDirectDisplayID?

    static func parse(_ arguments: [String]) throws -> Configuration {
        var mode: CaptureMode?
        var duration = 2.0
        var outputDirectory: URL?
        var evidenceURL: URL?
        var requestPermissions = true
        var displayID: CGDirectDisplayID?
        var index = 0

        func value(after flag: String) throws -> String {
            guard index + 1 < arguments.count else {
                throw SpikeError.usage("missing value after \(flag)")
            }
            index += 1
            return arguments[index]
        }

        while index < arguments.count {
            let argument = arguments[index]
            switch argument {
            case "--mode":
                let rawValue = try value(after: argument)
                guard let parsed = CaptureMode(rawValue: rawValue) else {
                    throw SpikeError.usage("--mode must be probe, mic, system, or both")
                }
                mode = parsed
            case "--duration":
                let rawValue = try value(after: argument)
                guard let parsed = Double(rawValue), parsed >= 0.1, parsed <= 3600 else {
                    throw SpikeError.usage("--duration must be between 0.1 and 3600 seconds")
                }
                duration = parsed
            case "--output-dir":
                outputDirectory = URL(fileURLWithPath: try value(after: argument), isDirectory: true)
            case "--evidence":
                evidenceURL = URL(fileURLWithPath: try value(after: argument), isDirectory: false)
            case "--display-id":
                let rawValue = try value(after: argument)
                guard let parsed = UInt32(rawValue) else {
                    throw SpikeError.usage("--display-id must be an unsigned integer")
                }
                displayID = parsed
            case "--request-permissions":
                requestPermissions = true
            case "--no-request-permissions":
                requestPermissions = false
            case "--help", "-h":
                throw SpikeError.usage(Self.usage)
            default:
                throw SpikeError.usage("unknown argument: \(argument)\n\n\(Self.usage)")
            }
            index += 1
        }

        guard let mode else {
            throw SpikeError.usage("--mode is required\n\n\(Self.usage)")
        }

        let timestamp = ISO8601DateFormatter.spikeFileName.string(from: Date())
        let resolvedOutput = outputDirectory ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("capture-\(timestamp)", isDirectory: true)
        let resolvedEvidence = evidenceURL ?? resolvedOutput.appendingPathComponent("evidence.json")

        return Configuration(
            mode: mode,
            durationSeconds: duration,
            outputDirectory: resolvedOutput,
            evidenceURL: resolvedEvidence,
            requestPermissions: requestPermissions,
            displayID: displayID
        )
    }

    static let usage = """
    Usage:
      macos-capture-spike --mode probe [--no-request-permissions] [--evidence PATH]
      macos-capture-spike --mode mic|system|both --duration SECONDS --output-dir DIR [options]

    Options:
      --request-permissions       Request missing permissions (default for capture modes).
      --no-request-permissions    Never display a permission prompt.
      --display-id ID             Capture a specific display for system audio.
      --evidence PATH             Write machine-readable JSON evidence to PATH.
    """
}

struct PermissionEvidence: Codable {
    var microphone: String
    var screenRecording: String
    var permissionRequestAllowed: Bool
}

struct TrackEvidence: Codable {
    let kind: String
    var status: String
    var filePath: String?
    var fileExists: Bool
    var fileBytes: UInt64
    var sampleRate: Double?
    var channels: UInt32?
    var frames: UInt64
    var errorCode: String?
    var errorMessage: String?
}

struct HostEvidence: Codable {
    let operatingSystem: String
    let architecture: String
    let processID: Int32
}

struct CaptureEvidence: Codable {
    let schemaVersion: String
    let spike: String
    let mode: String
    let requestedDurationSeconds: Double
    var actualDurationSeconds: Double
    let startedAt: String
    var endedAt: String
    var result: String
    var permissions: PermissionEvidence
    var tracks: [TrackEvidence]
    let host: HostEvidence
    let notes: [String]
}

final class TrackStats {
    private let lock = NSLock()
    private var storedFrames: UInt64 = 0
    private var storedSampleRate: Double?
    private var storedChannels: UInt32?
    private var storedError: Error?

    func add(frames: AVAudioFrameCount, sampleRate: Double, channels: AVAudioChannelCount) {
        lock.lock()
        storedFrames += UInt64(frames)
        storedSampleRate = sampleRate
        storedChannels = channels
        lock.unlock()
    }

    func fail(_ error: Error) {
        lock.lock()
        if storedError == nil { storedError = error }
        lock.unlock()
    }

    func snapshot() -> (frames: UInt64, sampleRate: Double?, channels: UInt32?, error: Error?) {
        lock.lock()
        defer { lock.unlock() }
        return (storedFrames, storedSampleRate, storedChannels, storedError)
    }
}

func waveSettings(for format: AVAudioFormat) -> [String: Any] {
    var settings = format.settings
    settings[AVLinearPCMIsNonInterleaved] = false
    return settings
}

final class MicrophoneRecorder {
    private let engine = AVAudioEngine()
    private var outputFile: AVAudioFile?
    let stats = TrackStats()

    func start(outputURL: URL) throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw SpikeError.capture("microphone input has no usable audio format")
        }

        outputFile = try AVAudioFile(forWriting: outputURL, settings: waveSettings(for: format))
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            guard let self, let outputFile = self.outputFile else { return }
            do {
                try outputFile.write(from: buffer)
                self.stats.add(
                    frames: buffer.frameLength,
                    sampleRate: buffer.format.sampleRate,
                    channels: buffer.format.channelCount
                )
            } catch {
                self.stats.fail(error)
            }
        }
        engine.prepare()
        try engine.start()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        outputFile = nil
    }
}

@available(macOS 13.0, *)
final class SystemAudioRecorder: NSObject, SCStreamOutput, SCStreamDelegate {
    private let outputQueue = DispatchQueue(label: "com.meetingcopilot.spike.system-audio")
    private let outputURL: URL
    private var stream: SCStream?
    private var outputFile: AVAudioFile?
    let stats = TrackStats()

    init(outputURL: URL) {
        self.outputURL = outputURL
    }

    func start(displayID: CGDirectDisplayID?) async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let displays = content.displays
        guard !displays.isEmpty else {
            throw SpikeError.capture("ScreenCaptureKit returned no capturable displays")
        }
        let display: SCDisplay
        if let displayID {
            guard let selected = displays.first(where: { $0.displayID == displayID }) else {
                throw SpikeError.capture("requested display \(displayID) is unavailable")
            }
            display = selected
        } else if let main = displays.first(where: { $0.displayID == CGMainDisplayID() }) {
            display = main
        } else {
            display = displays[0]
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 2
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.queueDepth = 3

        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: outputQueue)
        self.stream = stream
        try await stream.startCapture()
    }

    func stop() async {
        guard let stream else { return }
        do {
            try await stream.stopCapture()
        } catch {
            stats.fail(error)
        }
        do {
            try stream.removeStreamOutput(self, type: .audio)
        } catch {
            stats.fail(error)
        }
        outputQueue.sync {}
        self.stream = nil
        outputFile = nil
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard outputType == .audio, sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        do {
            try write(sampleBuffer)
        } catch {
            stats.fail(error)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        stats.fail(error)
    }

    private func write(_ sampleBuffer: CMSampleBuffer) throws {
        guard let description = CMSampleBufferGetFormatDescription(sampleBuffer) else {
            throw SpikeError.capture("system audio sample has no usable PCM format")
        }
        let format = AVAudioFormat(cmAudioFormatDescription: description)

        let frames = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard frames > 0 else { return }
        try sampleBuffer.withAudioBufferList(flags: [.audioBufferListAssure16ByteAlignment]) { bufferList, _ in
            guard let pcmBuffer = AVAudioPCMBuffer(
                pcmFormat: format,
                bufferListNoCopy: bufferList.unsafeMutablePointer,
                deallocator: nil
            ) else {
                throw SpikeError.capture("cannot map system audio sample to AVAudioPCMBuffer")
            }
            pcmBuffer.frameLength = frames

            if outputFile == nil {
                outputFile = try AVAudioFile(forWriting: outputURL, settings: waveSettings(for: format))
            }
            guard let outputFile else {
                throw SpikeError.capture("system audio output file was not created")
            }
            try outputFile.write(from: pcmBuffer)
        }
        stats.add(frames: frames, sampleRate: format.sampleRate, channels: format.channelCount)
    }
}

func microphonePermissionName(_ status: AVAuthorizationStatus) -> String {
    switch status {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

func requestMicrophonePermission() async -> Bool {
    await withCheckedContinuation { continuation in
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            continuation.resume(returning: granted)
        }
    }
}

func fileSize(_ url: URL) -> UInt64 {
    let values = try? url.resourceValues(forKeys: [.fileSizeKey])
    return UInt64(values?.fileSize ?? 0)
}

func architectureName() -> String {
    #if arch(arm64)
    return "arm64"
    #elseif arch(x86_64)
    return "x86_64"
    #else
    return "unknown"
    #endif
}

func trackEvidence(kind: String, outputURL: URL, stats: TrackStats, startError: Error?) -> TrackEvidence {
    let snapshot = stats.snapshot()
    let error = startError ?? snapshot.error
    let exists = FileManager.default.fileExists(atPath: outputURL.path)
    let status: String
    let errorCode: String?
    if startError != nil {
        status = "start_failed"
        errorCode = "capture_start_failed"
    } else if snapshot.error != nil {
        status = "write_failed"
        errorCode = "capture_write_failed"
    } else if snapshot.frames == 0 {
        status = "no_samples"
        errorCode = "capture_completed_without_samples"
    } else {
        status = "completed"
        errorCode = nil
    }
    return TrackEvidence(
        kind: kind,
        status: status,
        filePath: outputURL.path,
        fileExists: exists,
        fileBytes: exists ? fileSize(outputURL) : 0,
        sampleRate: snapshot.sampleRate,
        channels: snapshot.channels,
        frames: snapshot.frames,
        errorCode: errorCode,
        errorMessage: error.map(String.init(describing:))
    )
}

func writeEvidence(_ evidence: CaptureEvidence, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let data = try encoder.encode(evidence)
    try data.write(to: url, options: .atomic)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

@main
struct MacOSCaptureSpike {
    static func main() async {
        do {
            let configuration = try Configuration.parse(Array(CommandLine.arguments.dropFirst()))
            let exitCode = try await run(configuration)
            exit(exitCode)
        } catch SpikeError.usage(let message) {
            FileHandle.standardError.write(Data("\(message)\n".utf8))
            exit(message == Configuration.usage ? 0 : 64)
        } catch {
            FileHandle.standardError.write(Data("fatal: \(error)\n".utf8))
            exit(70)
        }
    }

    static func run(_ configuration: Configuration) async throws -> Int32 {
        let started = Date()
        let startedAt = ISO8601DateFormatter.spike.string(from: started)
        let initialMicrophoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        var permissions = PermissionEvidence(
            microphone: microphonePermissionName(initialMicrophoneStatus),
            screenRecording: CGPreflightScreenCaptureAccess() ? "authorized" : "not_authorized",
            permissionRequestAllowed: configuration.mode == .probe ? false : configuration.requestPermissions
        )
        var evidence = CaptureEvidence(
            schemaVersion: "meeting-copilot.macos-capture-spike.v1",
            spike: "phase0_native_capture",
            mode: configuration.mode.rawValue,
            requestedDurationSeconds: configuration.mode == .probe ? 0 : configuration.durationSeconds,
            actualDurationSeconds: 0,
            startedAt: startedAt,
            endedAt: startedAt,
            result: configuration.mode == .probe ? "probe_completed" : "running",
            permissions: permissions,
            tracks: [],
            host: HostEvidence(
                operatingSystem: ProcessInfo.processInfo.operatingSystemVersionString,
                architecture: architectureName(),
                processID: ProcessInfo.processInfo.processIdentifier
            ),
            notes: [
                "Microphone and system audio are intentionally written to separate tracks.",
                "Probe mode reads current permission state and never requests a permission prompt.",
                "This spike does not mix, transcribe, upload, or retain audio outside the requested output directory."
            ]
        )

        if configuration.mode == .probe {
            evidence.endedAt = ISO8601DateFormatter.spike.string(from: Date())
            try writeEvidence(evidence, to: configuration.evidenceURL)
            return 0
        }

        try FileManager.default.createDirectory(at: configuration.outputDirectory, withIntermediateDirectories: true)
        let microphoneURL = configuration.outputDirectory.appendingPathComponent("microphone.wav")
        let systemAudioURL = configuration.outputDirectory.appendingPathComponent("system-audio.wav")
        for url in [microphoneURL, systemAudioURL] where FileManager.default.fileExists(atPath: url.path) {
            throw SpikeError.capture("refusing to overwrite existing output: \(url.path)")
        }

        var microphoneRecorder: MicrophoneRecorder?
        var microphoneStartError: Error?
        if configuration.mode.wantsMicrophone {
            var status = AVCaptureDevice.authorizationStatus(for: .audio)
            if status == .notDetermined, configuration.requestPermissions {
                _ = await requestMicrophonePermission()
                status = AVCaptureDevice.authorizationStatus(for: .audio)
            }
            permissions.microphone = microphonePermissionName(status)
            if status == .authorized {
                let recorder = MicrophoneRecorder()
                do {
                    try recorder.start(outputURL: microphoneURL)
                    microphoneRecorder = recorder
                } catch {
                    microphoneRecorder = recorder
                    microphoneStartError = error
                }
            } else {
                let recorder = MicrophoneRecorder()
                microphoneRecorder = recorder
                microphoneStartError = SpikeError.permission("microphone permission is \(permissions.microphone)")
            }
        }

        var systemRecorder: SystemAudioRecorder?
        var systemStartError: Error?
        if configuration.mode.wantsSystemAudio {
            var authorized = CGPreflightScreenCaptureAccess()
            if !authorized, configuration.requestPermissions {
                authorized = CGRequestScreenCaptureAccess()
            }
            permissions.screenRecording = authorized ? "authorized" : "not_authorized"
            if authorized {
                if #available(macOS 13.0, *) {
                    let recorder = SystemAudioRecorder(outputURL: systemAudioURL)
                    do {
                        try await recorder.start(displayID: configuration.displayID)
                        systemRecorder = recorder
                    } catch {
                        systemRecorder = recorder
                        systemStartError = error
                    }
                } else {
                    systemStartError = SpikeError.capture("system audio capture requires macOS 13 or newer")
                }
            } else {
                systemStartError = SpikeError.permission("screen recording permission is not authorized")
            }
        }

        let anyTrackStarted = microphoneRecorder != nil && microphoneStartError == nil
            || systemRecorder != nil && systemStartError == nil
        if anyTrackStarted {
            let nanoseconds = UInt64(configuration.durationSeconds * 1_000_000_000)
            try? await Task.sleep(nanoseconds: nanoseconds)
        }

        microphoneRecorder?.stop()
        if #available(macOS 13.0, *) {
            await systemRecorder?.stop()
        }

        if configuration.mode.wantsMicrophone, let recorder = microphoneRecorder {
            evidence.tracks.append(trackEvidence(
                kind: "microphone",
                outputURL: microphoneURL,
                stats: recorder.stats,
                startError: microphoneStartError
            ))
        }
        if configuration.mode.wantsSystemAudio {
            if #available(macOS 13.0, *), let recorder = systemRecorder {
                evidence.tracks.append(trackEvidence(
                    kind: "system_audio",
                    outputURL: systemAudioURL,
                    stats: recorder.stats,
                    startError: systemStartError
                ))
            } else {
                evidence.tracks.append(TrackEvidence(
                    kind: "system_audio",
                    status: "start_failed",
                    filePath: systemAudioURL.path,
                    fileExists: false,
                    fileBytes: 0,
                    sampleRate: nil,
                    channels: nil,
                    frames: 0,
                    errorCode: "capture_start_failed",
                    errorMessage: systemStartError.map(String.init(describing:)) ?? "system recorder unavailable"
                ))
            }
        }

        let successfulTracks = evidence.tracks.filter { $0.status == "completed" }.count
        evidence.result = successfulTracks == evidence.tracks.count
            ? "completed"
            : successfulTracks > 0 ? "partial_failure" : "failed"
        evidence.permissions = permissions
        evidence.actualDurationSeconds = Date().timeIntervalSince(started)
        evidence.endedAt = ISO8601DateFormatter.spike.string(from: Date())
        try writeEvidence(evidence, to: configuration.evidenceURL)
        return evidence.result == "completed" ? 0 : 2
    }
}

extension ISO8601DateFormatter {
    static let spike: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    static let spikeFileName: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withYear, .withMonth, .withDay, .withTime]
        return formatter
    }()
}
