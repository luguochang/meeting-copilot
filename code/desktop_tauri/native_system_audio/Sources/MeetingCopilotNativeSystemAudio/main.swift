import AVFoundation
import CoreGraphics
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

private let protocolSchema = "meeting_copilot.native_system_audio_protocol.v1"
private let readySchema = "meeting_copilot.native_system_audio_ready.v1"
private let errorSchema = "meeting_copilot.native_system_audio_error.v1"
private let probeSchema = "meeting_copilot.native_system_audio_probe.v1"
private let outputSampleRate = 16_000.0
private let outputChannels: AVAudioChannelCount = 1
private let frameSamples = 4_800
private let frameBytes = frameSamples * MemoryLayout<Float>.size
private let audibleRMSThreshold: Float = 0.000_1
private let startupPCMTimeoutSeconds = 15.0
private let nativePCMProtocolName = "native_pcm_v2"
private let nativePCMMagic = Data([0x4d, 0x43, 0x50, 0x43, 0x4d, 0x32, 0x00, 0x00])

enum Operation {
    case describe
    case probe
    case stream
}

enum NativeSystemAudioError: Error, CustomStringConvertible {
    case usage(String)
    case nonLoopbackTransport
    case permissionDenied
    case noDisplays
    case displayUnavailable(CGDirectDisplayID)
    case invalidPCMFormat
    case noSamples
    case websocket(String)
    case capture(String)

    var code: String {
        switch self {
        case .usage: return "invalid_arguments"
        case .nonLoopbackTransport: return "non_loopback_transport_blocked"
        case .permissionDenied: return "permission_denied"
        case .noDisplays: return "content_unavailable"
        case .displayUnavailable: return "display_unavailable"
        case .invalidPCMFormat: return "invalid_pcm_format"
        case .noSamples: return "no_pcm_samples"
        case .websocket: return "loopback_websocket_failed"
        case .capture: return "capture_failed"
        }
    }

    var exitCode: Int32 {
        switch self {
        case .usage: return 64
        case .permissionDenied: return 77
        case .nonLoopbackTransport: return 78
        default: return 70
        }
    }

    var description: String {
        switch self {
        case .usage(let message): return message
        case .nonLoopbackTransport:
            return "system audio PCM transport must use authenticated ws://127.0.0.1 with an explicit port"
        case .permissionDenied:
            return "screen recording permission was denied; microphone fallback is disabled"
        case .noDisplays:
            return "ScreenCaptureKit returned no capturable displays"
        case .displayUnavailable(let displayID):
            return "requested display \(displayID) is unavailable"
        case .invalidPCMFormat:
            return "system audio could not be converted to 16 kHz mono Float32 PCM"
        case .noSamples:
            return "system audio capture returned no PCM samples"
        case .websocket(let message):
            return "loopback websocket error: \(message)"
        case .capture(let message):
            return "ScreenCaptureKit error: \(message)"
        }
    }
}

struct Configuration {
    let operation: Operation
    let webSocketURL: URL?
    let cookie: String
    let sessionID: String?
    let readyFile: URL?
    let displayID: CGDirectDisplayID?
    let durationSeconds: Double?
    let requestPermission: Bool
    let captureEpoch: UInt64?

    static let usage = """
    Usage:
      meeting-copilot-native-system-audio --describe
      meeting-copilot-native-system-audio --probe --duration SECONDS [--display-id ID] [--request-permission]
      meeting-copilot-native-system-audio --ws-url URL --session-id ID --ready-file PATH [--display-id ID] [--request-permission] [--duration SECONDS]

    Permission prompting is disabled unless --request-permission is explicitly supplied.
    """

    static func parse(_ arguments: [String]) throws -> Configuration {
        if arguments == ["--describe"] {
            return Configuration(
                operation: .describe,
                webSocketURL: nil,
                cookie: "",
                sessionID: nil,
                readyFile: nil,
                displayID: nil,
                durationSeconds: nil,
                requestPermission: false,
                captureEpoch: nil
            )
        }
        if arguments == ["--help"] || arguments == ["-h"] {
            throw NativeSystemAudioError.usage(Self.usage)
        }

        var operation: Operation = .stream
        var webSocketURL: URL?
        var sessionID: String?
        var readyFile: URL?
        var displayID: CGDirectDisplayID?
        var durationSeconds: Double?
        var requestPermission = false
        var index = 0

        func value(after flag: String) throws -> String {
            guard index + 1 < arguments.count else {
                throw NativeSystemAudioError.usage("missing value after \(flag)\n\n\(Self.usage)")
            }
            index += 1
            return arguments[index]
        }

        while index < arguments.count {
            switch arguments[index] {
            case "--probe":
                operation = .probe
            case "--ws-url":
                let rawValue = try value(after: "--ws-url")
                guard let parsed = URL(string: rawValue) else {
                    throw NativeSystemAudioError.usage("--ws-url is invalid")
                }
                try validateLoopbackWebSocket(parsed)
                webSocketURL = parsed
            case "--session-id":
                let value = try value(after: "--session-id")
                guard !value.isEmpty,
                      value.count <= 128,
                      value.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "_-.".contains($0)) }) else {
                    throw NativeSystemAudioError.usage("--session-id contains unsafe characters")
                }
                sessionID = value
            case "--ready-file":
                let value = try value(after: "--ready-file")
                guard value.hasPrefix("/") else {
                    throw NativeSystemAudioError.usage("--ready-file must be an absolute path")
                }
                readyFile = URL(fileURLWithPath: value, isDirectory: false)
            case "--display-id":
                let value = try value(after: "--display-id")
                guard let parsed = UInt32(value) else {
                    throw NativeSystemAudioError.usage("--display-id must be an unsigned integer")
                }
                displayID = parsed
            case "--duration":
                let value = try value(after: "--duration")
                guard let parsed = Double(value), parsed >= 0.5, parsed <= 3_600 else {
                    throw NativeSystemAudioError.usage("--duration must be between 0.5 and 3600 seconds")
                }
                durationSeconds = parsed
            case "--request-permission":
                requestPermission = true
            case "--no-request-permission":
                requestPermission = false
            default:
                throw NativeSystemAudioError.usage("unknown argument: \(arguments[index])\n\n\(Self.usage)")
            }
            index += 1
        }

        switch operation {
        case .probe:
            guard webSocketURL == nil, sessionID == nil, readyFile == nil else {
                throw NativeSystemAudioError.usage("--probe does not accept websocket session arguments")
            }
            guard durationSeconds != nil else {
                throw NativeSystemAudioError.usage("--probe requires --duration")
            }
        case .stream:
            guard webSocketURL != nil, sessionID != nil, readyFile != nil else {
                throw NativeSystemAudioError.usage("--ws-url, --session-id, and --ready-file are required")
            }
        case .describe:
            break
        }

        return Configuration(
            operation: operation,
            webSocketURL: webSocketURL,
            cookie: ProcessInfo.processInfo.environment["MEETING_COPILOT_SESSION_COOKIE"] ?? "",
            sessionID: sessionID,
            readyFile: readyFile,
            displayID: displayID,
            durationSeconds: durationSeconds,
            requestPermission: requestPermission,
            captureEpoch: try webSocketURL.map(nativeCaptureEpoch)
        )
    }
}

private func validateLoopbackWebSocket(_ url: URL) throws {
    guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
          components.scheme == "ws",
          components.host == "127.0.0.1",
          components.port != nil,
          components.user == nil,
          components.password == nil,
          components.path.hasPrefix("/live/asr/stream/ws/") else {
        throw NativeSystemAudioError.nonLoopbackTransport
    }
}

private func nativeCaptureEpoch(_ url: URL) throws -> UInt64 {
    guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
        throw NativeSystemAudioError.usage("--ws-url is invalid")
    }
    let values = Dictionary(
        grouping: components.queryItems ?? [],
        by: \.name
    ).mapValues { items in items.compactMap(\.value) }
    guard values["pcm_protocol"] == [nativePCMProtocolName],
          let epochValue = values["capture_epoch"]?.only,
          let epoch = UInt64(epochValue),
          epoch > 0 else {
        throw NativeSystemAudioError.usage(
            "--ws-url must declare pcm_protocol=native_pcm_v2 and a positive capture_epoch"
        )
    }
    return epoch
}

private extension Array {
    var only: Element? { count == 1 ? self[0] : nil }
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
    timestampMilliseconds: Int64,
    finalPartial: Bool
) throws -> Data {
    guard !pcm.isEmpty,
          pcm.count <= frameBytes,
          pcm.count.isMultiple(of: MemoryLayout<Float>.size),
          sequence > 0 else {
        throw NativeSystemAudioError.invalidPCMFormat
    }
    var envelope = Data(capacity: 44 + pcm.count)
    envelope.append(nativePCMMagic)
    envelope.append(2)
    envelope.append(2)
    appendBigEndian(UInt16(finalPartial ? 1 : 0), to: &envelope)
    appendBigEndian(captureEpoch, to: &envelope)
    appendBigEndian(sequence, to: &envelope)
    appendBigEndian(UInt64(max(0, timestampMilliseconds)), to: &envelope)
    appendBigEndian(UInt32(outputSampleRate), to: &envelope)
    appendBigEndian(UInt32(pcm.count), to: &envelope)
    envelope.append(pcm)
    return envelope
}

private final class JSONLineWriter {
    static let shared = JSONLineWriter()
    private let lock = NSLock()

    func stdout(_ payload: [String: Any]) {
        write(payload, handle: .standardOutput)
    }

    func stderr(_ payload: [String: Any]) {
        write(payload, handle: .standardError)
    }

    func diagnostic(_ message: String) {
        lock.lock()
        FileHandle.standardError.write(Data(("native_system_audio: \(message)\n").utf8))
        lock.unlock()
    }

    private func write(_ payload: [String: Any], handle: FileHandle) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else { return }
        lock.lock()
        handle.write(data)
        handle.write(Data("\n".utf8))
        lock.unlock()
    }
}

private func writeProtocolDescription() {
    JSONLineWriter.shared.stdout([
        "schema_version": protocolSchema,
        "source": "system_audio",
        "capture_framework": "ScreenCaptureKit",
        "minimum_macos": "13.0",
        "permission": "screen_recording",
        "sample_rate_hz": Int(outputSampleRate),
        "channels": Int(outputChannels),
        "sample_format": "pcm_f32le",
        "frame_samples": frameSamples,
        "transport": "authenticated_loopback_websocket",
        "pcm_protocol": nativePCMProtocolName,
        "pcm_envelope_header_bytes": 44,
        "pcm_envelope_track": "system_audio",
        "accepts_remote_websocket": false,
        "raw_audio_files_written": false,
        "excludes_current_process_audio": true,
        "fallback_source": NSNull(),
    ])
}

private func writeStructuredError(_ error: NativeSystemAudioError) {
    JSONLineWriter.shared.stderr([
        "schema_version": errorSchema,
        "error_code": error.code,
        "message": error.description,
        "source": "system_audio",
        "captured_audio": false,
        "raw_audio_files_written": false,
        "remote_upload_attempted": false,
        "fallback_source": NSNull(),
    ])
}

private func ensureScreenCapturePermission(requestAllowed: Bool) throws {
    if CGPreflightScreenCaptureAccess() { return }
    if requestAllowed, CGRequestScreenCaptureAccess() { return }
    throw NativeSystemAudioError.permissionDenied
}

struct ContentSelection {
    let displayID: CGDirectDisplayID
    let width: Int
    let height: Int
    let isMainDisplay: Bool
}

struct PCMFrame {
    let data: Data
    let samples: Int
    let rms: Float
    let inputPeakSample: Float?
    let inputPeakMetricStatus: String
    let inputCommonFormat: String
    let inputSampleRateHz: Double
    let inputChannelCount: Int
    let inputInterleaved: Bool
    let inputBufferCount: Int
    let inputByteCount: Int
    let inputBufferByteCounts: [Int]
    let inputFormatID: UInt32
    let inputFormatIDFourCC: String
    let inputFormatFlags: UInt32
    let inputBitsPerChannel: UInt32
    let inputBytesPerFrame: UInt32
    let inputFramesPerPacket: UInt32
    let rawNonzeroByteCount: Int?
    let rawByteMetricStatus: String
    let timestampMilliseconds: Int64
}

private struct PeakSampleMetric {
    let value: Float?
    let status: String
}

private struct RawByteMetric {
    let nonzeroByteCount: Int?
    let status: String
    let bufferByteCounts: [Int]
}

private func commonFormatName(_ format: AVAudioCommonFormat) -> String {
    switch format {
    case .otherFormat: return "other"
    case .pcmFormatFloat32: return "float32"
    case .pcmFormatFloat64: return "float64"
    case .pcmFormatInt16: return "int16"
    case .pcmFormatInt32: return "int32"
    @unknown default: return "unknown"
    }
}

private func peakSample(in buffer: AVAudioPCMBuffer) -> PeakSampleMetric {
    let frameCount = Int(buffer.frameLength)
    let channelCount = Int(buffer.format.channelCount)
    guard frameCount > 0, channelCount > 0 else {
        return PeakSampleMetric(value: nil, status: "metric_unavailable")
    }
    let bufferCount = buffer.format.isInterleaved ? 1 : channelCount
    let samplesPerBuffer = frameCount * (buffer.format.isInterleaved ? channelCount : 1)
    var peak: Float = 0

    switch buffer.format.commonFormat {
    case .pcmFormatFloat32:
        guard let channels = buffer.floatChannelData else {
            return PeakSampleMetric(value: nil, status: "metric_unavailable")
        }
        for bufferIndex in 0..<bufferCount {
            for sampleIndex in 0..<samplesPerBuffer {
                peak = max(peak, abs(channels[bufferIndex][sampleIndex]))
            }
        }
    case .pcmFormatInt16:
        guard let channels = buffer.int16ChannelData else {
            return PeakSampleMetric(value: nil, status: "metric_unavailable")
        }
        for bufferIndex in 0..<bufferCount {
            for sampleIndex in 0..<samplesPerBuffer {
                peak = max(peak, abs(Float(channels[bufferIndex][sampleIndex]) / 32_768))
            }
        }
    case .pcmFormatInt32:
        guard let channels = buffer.int32ChannelData else {
            return PeakSampleMetric(value: nil, status: "metric_unavailable")
        }
        for bufferIndex in 0..<bufferCount {
            for sampleIndex in 0..<samplesPerBuffer {
                peak = max(peak, abs(Float(channels[bufferIndex][sampleIndex]) / 2_147_483_648))
            }
        }
    case .pcmFormatFloat64, .otherFormat:
        return PeakSampleMetric(value: nil, status: "metric_unavailable")
    @unknown default:
        return PeakSampleMetric(value: nil, status: "metric_unavailable")
    }
    return PeakSampleMetric(value: max(0, min(1, peak)), status: "available")
}

private func rawByteMetric(
    in bufferList: UnsafeMutableAudioBufferListPointer
) -> RawByteMetric {
    var nonzeroByteCount = 0
    var metricAvailable = true
    var bufferByteCounts: [Int] = []
    bufferByteCounts.reserveCapacity(bufferList.count)

    for buffer in bufferList {
        let byteCount = Int(buffer.mDataByteSize)
        bufferByteCounts.append(byteCount)
        guard byteCount > 0 else { continue }
        guard let data = buffer.mData else {
            metricAvailable = false
            continue
        }
        let bytes = UnsafeRawBufferPointer(start: data, count: byteCount)
        nonzeroByteCount += bytes.reduce(into: 0) { count, byte in
            if byte != 0 { count += 1 }
        }
    }

    return RawByteMetric(
        nonzeroByteCount: metricAvailable ? nonzeroByteCount : nil,
        status: metricAvailable ? "available" : "metric_unavailable",
        bufferByteCounts: bufferByteCounts
    )
}

private func fourCC(_ value: UInt32) -> String {
    let bytes: [UInt8] = [
        UInt8((value >> 24) & 0xff),
        UInt8((value >> 16) & 0xff),
        UInt8((value >> 8) & 0xff),
        UInt8(value & 0xff),
    ]
    guard bytes.allSatisfy({ $0 >= 0x20 && $0 <= 0x7e }) else { return "non_printable" }
    return String(bytes: bytes, encoding: .ascii) ?? "non_printable"
}

final class SystemAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private let captureQueue = DispatchQueue(label: "com.meetingcopilot.native-system-audio.capture")
    private let onPCM: (PCMFrame) -> Void
    private let onFailure: (NativeSystemAudioError) -> Void
    private var stream: SCStream?
    private var converter: AVAudioConverter?
    private var stopping = false

    init(onPCM: @escaping (PCMFrame) -> Void, onFailure: @escaping (NativeSystemAudioError) -> Void) {
        self.onPCM = onPCM
        self.onFailure = onFailure
    }

    func start(displayID requestedDisplayID: CGDirectDisplayID?) async throws -> ContentSelection {
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                true,
                onScreenWindowsOnly: true
            )
        } catch {
            throw NativeSystemAudioError.capture(String(describing: error))
        }
        guard !content.displays.isEmpty else { throw NativeSystemAudioError.noDisplays }

        let display: SCDisplay
        if let requestedDisplayID {
            guard let selected = content.displays.first(where: { $0.displayID == requestedDisplayID }) else {
                throw NativeSystemAudioError.displayUnavailable(requestedDisplayID)
            }
            display = selected
        } else if let main = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) {
            display = main
        } else {
            display = content.displays[0]
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 2
        configuration.width = 2
        configuration.height = 2
        configuration.showsCursor = false
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.queueDepth = 3

        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        do {
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: captureQueue)
            self.stream = stream
            try await stream.startCapture()
        } catch {
            self.stream = nil
            try? stream.removeStreamOutput(self, type: .audio)
            throw NativeSystemAudioError.capture(String(describing: error))
        }
        return ContentSelection(
            displayID: display.displayID,
            width: display.width,
            height: display.height,
            isMainDisplay: display.displayID == CGMainDisplayID()
        )
    }

    func stop() async {
        guard !stopping else { return }
        stopping = true
        guard let stream else { return }
        do {
            try await stream.stopCapture()
        } catch {
            JSONLineWriter.shared.diagnostic("stage=stop_capture_failed error=\(error)")
        }
        try? stream.removeStreamOutput(self, type: .audio)
        captureQueue.sync {}
        self.stream = nil
        converter = nil
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .audio,
              sampleBuffer.isValid,
              CMSampleBufferDataIsReady(sampleBuffer) else { return }
        do {
            try convert(sampleBuffer)
        } catch let error as NativeSystemAudioError {
            onFailure(error)
        } catch {
            onFailure(.capture(String(describing: error)))
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        if !stopping {
            onFailure(.capture(String(describing: error)))
        }
    }

    private func convert(_ sampleBuffer: CMSampleBuffer) throws {
        guard let description = CMSampleBufferGetFormatDescription(sampleBuffer) else {
            throw NativeSystemAudioError.invalidPCMFormat
        }
        guard let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee else {
            throw NativeSystemAudioError.invalidPCMFormat
        }
        let inputFormat = AVAudioFormat(cmAudioFormatDescription: description)
        let inputFrames = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard inputFrames > 0 else { return }

        try sampleBuffer.withAudioBufferList(flags: [.audioBufferListAssure16ByteAlignment]) { bufferList, _ in
            let rawMetric = rawByteMetric(in: bufferList)
            let inputBufferCount = bufferList.count
            let inputByteCount = bufferList.reduce(0) { total, buffer in
                total + Int(buffer.mDataByteSize)
            }
            guard let input = AVAudioPCMBuffer(
                pcmFormat: inputFormat,
                bufferListNoCopy: bufferList.unsafeMutablePointer,
                deallocator: nil
            ) else {
                throw NativeSystemAudioError.invalidPCMFormat
            }
            input.frameLength = inputFrames
            try convert(
                input,
                presentationTime: CMSampleBufferGetPresentationTimeStamp(sampleBuffer),
                inputBufferCount: inputBufferCount,
                inputByteCount: inputByteCount,
                inputBufferByteCounts: rawMetric.bufferByteCounts,
                streamDescription: streamDescription,
                rawNonzeroByteCount: rawMetric.nonzeroByteCount,
                rawByteMetricStatus: rawMetric.status
            )
        }
    }

    private func convert(
        _ input: AVAudioPCMBuffer,
        presentationTime: CMTime,
        inputBufferCount: Int,
        inputByteCount: Int,
        inputBufferByteCounts: [Int],
        streamDescription: AudioStreamBasicDescription,
        rawNonzeroByteCount: Int?,
        rawByteMetricStatus: String
    ) throws {
        let inputPeakMetric = peakSample(in: input)
        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: outputSampleRate,
            channels: outputChannels,
            interleaved: false
        ) else {
            throw NativeSystemAudioError.invalidPCMFormat
        }
        if converter == nil || converter?.inputFormat != input.format {
            converter = AVAudioConverter(from: input.format, to: outputFormat)
        }
        guard let converter,
              let output = AVAudioPCMBuffer(
                  pcmFormat: outputFormat,
                  frameCapacity: AVAudioFrameCount(
                      ceil(Double(input.frameLength) * outputSampleRate / input.format.sampleRate)
                  ) + 64
              ) else {
            throw NativeSystemAudioError.invalidPCMFormat
        }

        var supplied = false
        var conversionError: NSError?
        let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
            if supplied {
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return input
        }
        guard status != .error,
              conversionError == nil,
              output.frameLength > 0,
              let channel = output.floatChannelData?[0] else {
            if let conversionError {
                throw NativeSystemAudioError.capture(conversionError.localizedDescription)
            }
            return
        }

        var sumSquares: Float = 0
        for index in 0..<Int(output.frameLength) {
            let sample = channel[index]
            sumSquares += sample * sample
        }
        let rms = sqrt(sumSquares / Float(output.frameLength))
        let seconds = CMTimeGetSeconds(presentationTime)
        let timestamp = seconds.isFinite ? Int64((seconds * 1_000).rounded()) : 0
        onPCM(PCMFrame(
            data: Data(bytes: channel, count: Int(output.frameLength) * MemoryLayout<Float>.size),
            samples: Int(output.frameLength),
            rms: max(0, min(1, rms)),
            inputPeakSample: inputPeakMetric.value,
            inputPeakMetricStatus: inputPeakMetric.status,
            inputCommonFormat: commonFormatName(input.format.commonFormat),
            inputSampleRateHz: input.format.sampleRate,
            inputChannelCount: Int(input.format.channelCount),
            inputInterleaved: input.format.isInterleaved,
            inputBufferCount: inputBufferCount,
            inputByteCount: inputByteCount,
            inputBufferByteCounts: inputBufferByteCounts,
            inputFormatID: streamDescription.mFormatID,
            inputFormatIDFourCC: fourCC(streamDescription.mFormatID),
            inputFormatFlags: streamDescription.mFormatFlags,
            inputBitsPerChannel: streamDescription.mBitsPerChannel,
            inputBytesPerFrame: streamDescription.mBytesPerFrame,
            inputFramesPerPacket: streamDescription.mFramesPerPacket,
            rawNonzeroByteCount: rawNonzeroByteCount,
            rawByteMetricStatus: rawByteMetricStatus,
            timestampMilliseconds: timestamp
        ))
    }
}

final class WebSocketDelegate: NSObject, URLSessionWebSocketDelegate {
    let opened = DispatchSemaphore(value: 0)

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        opened.signal()
    }
}

private struct StreamStartupSnapshot {
    let transportReady: Bool
    let pcmSeen: Bool
    let audiblePCMSeen: Bool
    let firstPCMRMS: Float?
    let pcmBytesSent: UInt64
    let readyWritten: Bool
    let failure: NativeSystemAudioError?
}

final class SystemAudioStreamer {
    private let configuration: Configuration
    private let sendQueue = DispatchQueue(label: "com.meetingcopilot.native-system-audio.send")
    private let stateLock = NSLock()
    private var capture: SystemAudioCapture!
    private var webSocketSession: URLSession?
    private var webSocketTask: URLSessionWebSocketTask?
    private var pingTimer: DispatchSourceTimer?
    private var pendingPCM = Data()
    private var stopping = false
    private var stopped = false
    private var pcmSequence: UInt64 = 0
    private var lastPCMTimestampMilliseconds: Int64 = 0
    private var lastPCMRMS: Float = 0
    private var transportReady = false
    private var pcmSeen = false
    private var audiblePCMSeen = false
    private var firstPCMRMS: Float?
    private var pcmBytesSent: UInt64 = 0
    private var readyWritten = false
    private var terminalFailure: NativeSystemAudioError?

    init(configuration: Configuration) {
        self.configuration = configuration
        capture = SystemAudioCapture(
            onPCM: { [weak self] frame in self?.handlePCM(frame) },
            onFailure: { [weak self] error in self?.captureFailed(error) }
        )
    }

    func start() async throws {
        JSONLineWriter.shared.diagnostic("stage=request_permission allowed=\(configuration.requestPermission)")
        try ensureScreenCapturePermission(requestAllowed: configuration.requestPermission)
        JSONLineWriter.shared.diagnostic("stage=permission_granted")
        try connectWebSocket()
        do {
            let selection = try await capture.start(displayID: configuration.displayID)
            writeContentSelected(selection)
            try await waitForTransportedPCMAndWriteReady(selection)
        } catch {
            await stop()
            throw error
        }
    }

    func stop() async {
        guard beginStopping() else { return }

        await capture.stop()
        pingTimer?.cancel()
        pingTimer = nil
        sendQueue.sync {
            guard let task = webSocketTask else { return }
            if !pendingPCM.isEmpty {
                do {
                    pcmSequence += 1
                    try sendBinary(
                        pendingPCM,
                        sequence: pcmSequence,
                        timestampMilliseconds: lastPCMTimestampMilliseconds,
                        finalPartial: true,
                        task: task
                    )
                    recordSuccessfulPCMSend(byteCount: pendingPCM.count, rms: lastPCMRMS)
                } catch {
                    JSONLineWriter.shared.diagnostic("stage=websocket_final_flush_failed error=\(error)")
                }
                pendingPCM.removeAll(keepingCapacity: false)
            }
            let semaphore = DispatchSemaphore(value: 0)
            task.send(.string("END")) { _ in semaphore.signal() }
            _ = semaphore.wait(timeout: .now() + 2.0)
        }
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketSession?.invalidateAndCancel()
        markStopped()
        JSONLineWriter.shared.stdout([
            "event_type": "stopped",
            "source": "system_audio",
            "pcm_sequence": pcmSequence,
            "raw_audio_files_written": false,
        ])
    }

    private func connectWebSocket() throws {
        guard let url = configuration.webSocketURL else {
            throw NativeSystemAudioError.usage("--ws-url is required")
        }
        try validateLoopbackWebSocket(url)
        let delegate = WebSocketDelegate()
        let sessionConfiguration = URLSessionConfiguration.ephemeral
        sessionConfiguration.timeoutIntervalForRequest = 120
        sessionConfiguration.timeoutIntervalForResource = 24 * 60 * 60
        let session = URLSession(configuration: sessionConfiguration, delegate: delegate, delegateQueue: nil)
        var request = URLRequest(url: url)
        if !configuration.cookie.isEmpty {
            request.setValue(configuration.cookie, forHTTPHeaderField: "Cookie")
        }
        let task = session.webSocketTask(with: request)
        webSocketSession = session
        webSocketTask = task
        task.resume()
        guard delegate.opened.wait(timeout: .now() + 15.0) == .success else {
            task.cancel(with: .goingAway, reason: nil)
            throw NativeSystemAudioError.websocket("connection timed out")
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
            task.sendPing { error in
                if let error {
                    JSONLineWriter.shared.diagnostic("stage=websocket_ping_failed error=\(error)")
                    self.captureFailed(.websocket(String(describing: error)))
                }
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
                if case .string(let text) = message,
                   let data = text.data(using: .utf8),
                   let object = try? JSONSerialization.jsonObject(with: data),
                   let payload = object as? [String: Any] {
                    JSONLineWriter.shared.stdout(payload)
                }
                self.receiveLoop(task)
            case .failure(let error):
                if !self.isInactive() {
                    JSONLineWriter.shared.diagnostic("stage=websocket_receive_failed error=\(error)")
                    self.captureFailed(.websocket(String(describing: error)))
                }
            }
        }
    }

    private func handlePCM(_ frame: PCMFrame) {
        if isInactive() { return }
        sendQueue.async { [weak self] in
            guard let self, let task = self.webSocketTask, !self.isInactive() else { return }
            self.pendingPCM.append(frame.data)
            self.lastPCMTimestampMilliseconds = frame.timestampMilliseconds
            self.lastPCMRMS = frame.rms
            while self.pendingPCM.count >= frameBytes {
                let data = Data(self.pendingPCM.prefix(frameBytes))
                self.pendingPCM.removeFirst(frameBytes)
                self.pcmSequence += 1
                do {
                    try self.sendBinary(
                        data,
                        sequence: self.pcmSequence,
                        timestampMilliseconds: frame.timestampMilliseconds,
                        finalPartial: false,
                        task: task
                    )
                } catch let error as NativeSystemAudioError {
                    self.captureFailed(error)
                    return
                } catch {
                    self.captureFailed(.websocket(String(describing: error)))
                    return
                }
                self.recordSuccessfulPCMSend(byteCount: data.count, rms: frame.rms)
                JSONLineWriter.shared.stdout([
                    "event_type": "pcm",
                    "source": "system_audio",
                    "sequence": self.pcmSequence,
                    "timestamp_ms": frame.timestampMilliseconds,
                    "frame_samples": frameSamples,
                    "sample_rate_hz": Int(outputSampleRate),
                    "channels": Int(outputChannels),
                    "sample_format": "pcm_f32le",
                    "rms": frame.rms,
                    "input_peak_sample": frame.inputPeakSample ?? NSNull(),
                    "input_peak_metric_status": frame.inputPeakMetricStatus,
                    "input_common_format": frame.inputCommonFormat,
                    "input_sample_rate_hz": frame.inputSampleRateHz,
                    "input_channel_count": frame.inputChannelCount,
                    "input_interleaved": frame.inputInterleaved,
                    "input_buffer_count": frame.inputBufferCount,
                    "input_byte_count": frame.inputByteCount,
                    "input_buffer_byte_counts": frame.inputBufferByteCounts,
                    "input_format_id": frame.inputFormatID,
                    "input_format_id_fourcc": frame.inputFormatIDFourCC,
                    "input_format_flags": frame.inputFormatFlags,
                    "input_bits_per_channel": frame.inputBitsPerChannel,
                    "input_bytes_per_frame": frame.inputBytesPerFrame,
                    "input_frames_per_packet": frame.inputFramesPerPacket,
                    "raw_nonzero_byte_count": frame.rawNonzeroByteCount ?? NSNull(),
                    "raw_byte_metric_status": frame.rawByteMetricStatus,
                    "raw_pcm_in_event": false,
                ])
            }
        }
    }

    private func sendBinary(
        _ data: Data,
        sequence: UInt64,
        timestampMilliseconds: Int64,
        finalPartial: Bool,
        task: URLSessionWebSocketTask
    ) throws {
        guard let captureEpoch = configuration.captureEpoch else {
            throw NativeSystemAudioError.usage("native PCM capture epoch is missing")
        }
        let envelope = try nativePCMEnvelope(
            pcm: data,
            captureEpoch: captureEpoch,
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
            throw NativeSystemAudioError.websocket("binary PCM send timed out")
        } else if let sendError {
            throw NativeSystemAudioError.websocket("binary PCM send failed: \(sendError)")
        }
    }

    private func writeContentSelected(_ selection: ContentSelection) {
        JSONLineWriter.shared.stdout([
            "event_type": "content_selected",
            "source": "system_audio",
            "display_id": selection.displayID,
            "display_width": selection.width,
            "display_height": selection.height,
            "is_main_display": selection.isMainDisplay,
            "excludes_current_process_audio": true,
        ])
    }

    private func waitForTransportedPCMAndWriteReady(_ selection: ContentSelection) async throws {
        let deadline = Date().addingTimeInterval(startupPCMTimeoutSeconds)
        while true {
            let snapshot = startupSnapshot()
            if let failure = snapshot.failure { throw failure }
            if snapshot.transportReady && snapshot.pcmSeen {
                try writeReady(selection, startup: snapshot)
                let committed = markReadyWrittenUnlessFailed()
                if let failure = committed.failure {
                    removeReadyFile()
                    throw failure
                }
                if committed.readyWritten { return }
            }
            if Date() >= deadline {
                throw NativeSystemAudioError.noSamples
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
    }

    private func writeReady(
        _ selection: ContentSelection,
        startup: StreamStartupSnapshot
    ) throws {
        guard let readyFile = configuration.readyFile,
              let sessionID = configuration.sessionID else {
            throw NativeSystemAudioError.usage("--ready-file and --session-id are required")
        }
        let payload: [String: Any] = [
            "schema_version": readySchema,
            "status": "ready",
            "session_id": sessionID,
            "source": "system_audio",
            "capture_framework": "ScreenCaptureKit",
            "permission": "authorized",
            "display_id": selection.displayID,
            "sample_rate_hz": Int(outputSampleRate),
            "channels": Int(outputChannels),
            "sample_format": "pcm_f32le",
            "frame_samples": frameSamples,
            "excludes_current_process_audio": true,
            "raw_audio_files_written": false,
            "remote_upload_allowed": false,
            "transport_ready": startup.transportReady,
            "pcm_seen": startup.pcmSeen,
            "audible_pcm_seen": startup.audiblePCMSeen,
            "first_pcm_rms": startup.firstPCMRMS ?? NSNull(),
            "pcm_bytes_sent": startup.pcmBytesSent,
            "pcm_protocol": nativePCMProtocolName,
            "capture_epoch": configuration.captureEpoch ?? 0,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else {
            throw NativeSystemAudioError.capture("ready payload could not be encoded")
        }
        do {
            try FileManager.default.createDirectory(
                at: readyFile.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try data.write(to: readyFile, options: .atomic)
        } catch {
            throw NativeSystemAudioError.capture("ready file could not be written: \(error)")
        }
    }

    private func captureFailed(_ error: NativeSystemAudioError) {
        let shouldExit = recordTerminalFailure(error)
        guard shouldExit else { return }
        removeReadyFile()
        writeStructuredError(error)
        DispatchQueue.main.async { [weak self] in
            guard let self else { exit(error.exitCode) }
            Task {
                await self.stop()
                exit(error.exitCode)
            }
        }
    }

    private func isInactive() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return stopping || stopped
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
        audiblePCMSeen = audiblePCMSeen || rms >= audibleRMSThreshold
        let (updatedBytes, overflow) = pcmBytesSent.addingReportingOverflow(UInt64(byteCount))
        pcmBytesSent = overflow ? UInt64.max : updatedBytes
        stateLock.unlock()
    }

    private func startupSnapshot() -> StreamStartupSnapshot {
        stateLock.lock()
        defer { stateLock.unlock() }
        return StreamStartupSnapshot(
            transportReady: transportReady,
            pcmSeen: pcmSeen,
            audiblePCMSeen: audiblePCMSeen,
            firstPCMRMS: firstPCMRMS,
            pcmBytesSent: pcmBytesSent,
            readyWritten: readyWritten,
            failure: terminalFailure
        )
    }

    private func markReadyWrittenUnlessFailed() -> StreamStartupSnapshot {
        stateLock.lock()
        if terminalFailure == nil && !stopping && !stopped {
            readyWritten = true
        }
        let snapshot = StreamStartupSnapshot(
            transportReady: transportReady,
            pcmSeen: pcmSeen,
            audiblePCMSeen: audiblePCMSeen,
            firstPCMRMS: firstPCMRMS,
            pcmBytesSent: pcmBytesSent,
            readyWritten: readyWritten,
            failure: terminalFailure
        )
        stateLock.unlock()
        return snapshot
    }

    private func recordTerminalFailure(_ error: NativeSystemAudioError) -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        if stopping || stopped { return false }
        if terminalFailure == nil { terminalFailure = error }
        return readyWritten
    }

    private func removeReadyFile() {
        guard let readyFile = configuration.readyFile else { return }
        try? FileManager.default.removeItem(at: readyFile)
    }

    private func beginStopping() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        if stopping || stopped { return false }
        stopping = true
        return true
    }

    private func markStopped() {
        stateLock.lock()
        stopping = false
        stopped = true
        stateLock.unlock()
    }
}

final class SystemAudioProbe {
    private let configuration: Configuration
    private let stateLock = NSLock()
    private var capture: SystemAudioCapture!
    private var pcmEventCount: UInt64 = 0
    private var frames: UInt64 = 0
    private var peakRMS: Float = 0
    private var inputPeakSample: Float?
    private var inputPeakMetricStatus = "metric_unavailable"
    private var nonzeroPCMEventCount: UInt64 = 0
    private var inputCommonFormat: String?
    private var inputSampleRateHz: Double = 0
    private var inputChannelCount: Int = 0
    private var inputInterleaved = false
    private var inputBufferCount: Int = 0
    private var inputByteCount: Int = 0
    private var inputBufferByteCounts: [Int] = []
    private var inputFormatID: UInt32 = 0
    private var inputFormatIDFourCC = "unknown"
    private var inputFormatFlags: UInt32 = 0
    private var inputBitsPerChannel: UInt32 = 0
    private var inputBytesPerFrame: UInt32 = 0
    private var inputFramesPerPacket: UInt32 = 0
    private var rawNonzeroByteCount: UInt64? = 0
    private var rawByteMetricStatus = "available"
    private var failure: NativeSystemAudioError?

    init(configuration: Configuration) {
        self.configuration = configuration
        capture = SystemAudioCapture(
            onPCM: { [weak self] frame in self?.accumulate(frame) },
            onFailure: { [weak self] error in self?.recordFailure(error) }
        )
    }

    func run() async throws {
        guard let duration = configuration.durationSeconds else {
            throw NativeSystemAudioError.usage("--probe requires --duration")
        }
        try ensureScreenCapturePermission(requestAllowed: configuration.requestPermission)
        let selection = try await capture.start(displayID: configuration.displayID)
        try await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
        await capture.stop()

        let result = snapshot()
        if let captureFailure = result.failure { throw captureFailure }
        guard result.pcmEventCount > 0, result.frames > 0 else { throw NativeSystemAudioError.noSamples }
        JSONLineWriter.shared.stdout([
            "schema_version": probeSchema,
            "status": "passed",
            "source": "system_audio",
            "permission": "authorized",
            "capture_framework": "ScreenCaptureKit",
            "display_id": selection.displayID,
            "is_main_display": selection.isMainDisplay,
            "pcm_event_count": result.pcmEventCount,
            "frames": result.frames,
            "peak_rms": result.peakRMS,
            "input_peak_sample": result.inputPeakSample ?? NSNull(),
            "input_peak_metric_status": result.inputPeakMetricStatus,
            "nonzero_pcm_event_count": result.nonzeroPCMEventCount,
            "silent_pcm_event_count": result.pcmEventCount - result.nonzeroPCMEventCount,
            "input_common_format": result.inputCommonFormat,
            "input_sample_rate_hz": result.inputSampleRateHz,
            "input_channel_count": result.inputChannelCount,
            "input_interleaved": result.inputInterleaved,
            "input_buffer_count": result.inputBufferCount,
            "input_byte_count": result.inputByteCount,
            "input_buffer_byte_counts": result.inputBufferByteCounts,
            "input_format_id": result.inputFormatID,
            "input_format_id_fourcc": result.inputFormatIDFourCC,
            "input_format_flags": result.inputFormatFlags,
            "input_bits_per_channel": result.inputBitsPerChannel,
            "input_bytes_per_frame": result.inputBytesPerFrame,
            "input_frames_per_packet": result.inputFramesPerPacket,
            "raw_nonzero_byte_count": result.rawNonzeroByteCount ?? NSNull(),
            "raw_byte_metric_status": result.rawByteMetricStatus,
            "sample_rate_hz": Int(outputSampleRate),
            "channels": Int(outputChannels),
            "sample_format": "pcm_f32le",
            "raw_audio_files_written": false,
            "remote_upload_attempted": false,
            "fallback_source": NSNull(),
        ])
    }

    func stop() async {
        await capture.stop()
    }

    private func accumulate(_ frame: PCMFrame) {
        stateLock.lock()
        pcmEventCount += 1
        frames += UInt64(frame.samples)
        peakRMS = max(peakRMS, frame.rms)
        if let frameInputPeak = frame.inputPeakSample {
            inputPeakSample = max(inputPeakSample ?? 0, frameInputPeak)
            inputPeakMetricStatus = "available"
        }
        if frame.rms > 0 { nonzeroPCMEventCount += 1 }
        if frame.rawByteMetricStatus == "available", let frameRawCount = frame.rawNonzeroByteCount {
            if let current = rawNonzeroByteCount {
                let (updated, overflow) = current.addingReportingOverflow(UInt64(frameRawCount))
                rawNonzeroByteCount = overflow ? UInt64.max : updated
            }
        } else {
            rawNonzeroByteCount = nil
            rawByteMetricStatus = "metric_unavailable"
        }
        if inputCommonFormat == nil {
            inputCommonFormat = frame.inputCommonFormat
            inputSampleRateHz = frame.inputSampleRateHz
            inputChannelCount = frame.inputChannelCount
            inputInterleaved = frame.inputInterleaved
            inputBufferCount = frame.inputBufferCount
            inputByteCount = frame.inputByteCount
            inputBufferByteCounts = frame.inputBufferByteCounts
            inputFormatID = frame.inputFormatID
            inputFormatIDFourCC = frame.inputFormatIDFourCC
            inputFormatFlags = frame.inputFormatFlags
            inputBitsPerChannel = frame.inputBitsPerChannel
            inputBytesPerFrame = frame.inputBytesPerFrame
            inputFramesPerPacket = frame.inputFramesPerPacket
        }
        stateLock.unlock()
    }

    private func recordFailure(_ error: NativeSystemAudioError) {
        stateLock.lock()
        if failure == nil { failure = error }
        stateLock.unlock()
    }

    private func snapshot() -> SystemAudioProbeSnapshot {
        stateLock.lock()
        defer { stateLock.unlock() }
        return SystemAudioProbeSnapshot(
            pcmEventCount: pcmEventCount,
            frames: frames,
            peakRMS: peakRMS,
            inputPeakSample: inputPeakSample,
            inputPeakMetricStatus: inputPeakMetricStatus,
            nonzeroPCMEventCount: nonzeroPCMEventCount,
            inputCommonFormat: inputCommonFormat ?? "unknown",
            inputSampleRateHz: inputSampleRateHz,
            inputChannelCount: inputChannelCount,
            inputInterleaved: inputInterleaved,
            inputBufferCount: inputBufferCount,
            inputByteCount: inputByteCount,
            inputBufferByteCounts: inputBufferByteCounts,
            inputFormatID: inputFormatID,
            inputFormatIDFourCC: inputFormatIDFourCC,
            inputFormatFlags: inputFormatFlags,
            inputBitsPerChannel: inputBitsPerChannel,
            inputBytesPerFrame: inputBytesPerFrame,
            inputFramesPerPacket: inputFramesPerPacket,
            rawNonzeroByteCount: rawNonzeroByteCount,
            rawByteMetricStatus: rawByteMetricStatus,
            failure: failure
        )
    }
}

private struct SystemAudioProbeSnapshot {
    let pcmEventCount: UInt64
    let frames: UInt64
    let peakRMS: Float
    let inputPeakSample: Float?
    let inputPeakMetricStatus: String
    let nonzeroPCMEventCount: UInt64
    let inputCommonFormat: String
    let inputSampleRateHz: Double
    let inputChannelCount: Int
    let inputInterleaved: Bool
    let inputBufferCount: Int
    let inputByteCount: Int
    let inputBufferByteCounts: [Int]
    let inputFormatID: UInt32
    let inputFormatIDFourCC: String
    let inputFormatFlags: UInt32
    let inputBitsPerChannel: UInt32
    let inputBytesPerFrame: UInt32
    let inputFramesPerPacket: UInt32
    let rawNonzeroByteCount: UInt64?
    let rawByteMetricStatus: String
    let failure: NativeSystemAudioError?
}

@main
struct MeetingCopilotNativeSystemAudioMain {
    static func main() {
        do {
            let configuration = try Configuration.parse(Array(CommandLine.arguments.dropFirst()))
            if configuration.operation == .describe {
                writeProtocolDescription()
                exit(0)
            }

            signal(SIGTERM, SIG_IGN)
            switch configuration.operation {
            case .probe:
                let probe = SystemAudioProbe(configuration: configuration)
                let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
                termination.setEventHandler {
                    Task {
                        await probe.stop()
                        exit(0)
                    }
                }
                termination.resume()
                Task {
                    do {
                        try await probe.run()
                        exit(0)
                    } catch let error as NativeSystemAudioError {
                        writeStructuredError(error)
                        exit(error.exitCode)
                    } catch {
                        let wrapped = NativeSystemAudioError.capture(String(describing: error))
                        writeStructuredError(wrapped)
                        exit(wrapped.exitCode)
                    }
                }
            case .stream:
                let streamer = SystemAudioStreamer(configuration: configuration)
                let parentPID = getppid()
                let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
                termination.setEventHandler {
                    Task {
                        await streamer.stop()
                        exit(0)
                    }
                }
                termination.resume()
                let parentMonitor = DispatchSource.makeTimerSource(queue: .main)
                parentMonitor.schedule(deadline: .now() + 1.0, repeating: 1.0)
                parentMonitor.setEventHandler {
                    if getppid() != parentPID || kill(parentPID, 0) != 0 {
                        Task {
                            await streamer.stop()
                            exit(0)
                        }
                    }
                }
                parentMonitor.resume()
                Task {
                    do {
                        try await streamer.start()
                        if let duration = configuration.durationSeconds {
                            try await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
                            await streamer.stop()
                            exit(0)
                        }
                    } catch let error as NativeSystemAudioError {
                        writeStructuredError(error)
                        await streamer.stop()
                        exit(error.exitCode)
                    } catch {
                        let wrapped = NativeSystemAudioError.capture(String(describing: error))
                        writeStructuredError(wrapped)
                        await streamer.stop()
                        exit(wrapped.exitCode)
                    }
                }
            case .describe:
                break
            }
            dispatchMain()
        } catch let error as NativeSystemAudioError {
            if case .usage(let message) = error, message == Configuration.usage {
                FileHandle.standardOutput.write(Data((message + "\n").utf8))
                exit(0)
            }
            writeStructuredError(error)
            exit(error.exitCode)
        } catch {
            let wrapped = NativeSystemAudioError.capture(String(describing: error))
            writeStructuredError(wrapped)
            exit(wrapped.exitCode)
        }
    }
}
