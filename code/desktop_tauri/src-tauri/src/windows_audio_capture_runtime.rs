//! Windows WASAPI endpoint discovery and authenticated PCM capture.

use crate::desktop_backend_supervisor::{BackendSupervisor, BackendWebSocketConnection};
use crate::private_storage::{ensure_private_directory, open_private_file};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::VecDeque;
use std::fmt;
use std::fs::{self, File};
use std::io::{ErrorKind, Write};
use std::mem::size_of;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
#[cfg(test)]
use tungstenite::accept;
use tungstenite::client::IntoClientRequest;
use tungstenite::http::header::{HeaderValue, COOKIE};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message, WebSocket};
use windows::core::{HRESULT, HSTRING, PWSTR};
use windows::Win32::Devices::FunctionDiscovery::PKEY_Device_FriendlyName;
use windows::Win32::Foundation::RPC_E_CHANGED_MODE;
use windows::Win32::Media::Audio::{
    eCapture, eCommunications, eConsole, eRender, EDataFlow, IAudioCaptureClient, IAudioClient,
    IMMDevice, IMMDeviceEnumerator, MMDeviceEnumerator, AUDCLNT_BUFFERFLAGS_SILENT,
    AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, DEVICE_STATE, DEVICE_STATE_ACTIVE,
    WAVEFORMATEX, WAVEFORMATEXTENSIBLE,
};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemFree, CLSCTX_ALL, COINIT_MULTITHREADED, STGM_READ,
};

const ERROR_NO_AUDIO_ENDPOINTS: &str = "no active Windows audio endpoints are available";
const NATIVE_SAMPLE_RATE_HZ: u32 = 16_000;
const NATIVE_FRAME_SAMPLES: usize = 4_800;
const NATIVE_PCM_HEADER_SIZE: usize = 44;
const WAVE_FORMAT_EXTENSIBLE: u16 = 0xfffe;
const START_TIMEOUT: Duration = Duration::from_secs(15);
const STOP_TIMEOUT: Duration = Duration::from_secs(20);
const EVENT_LIMIT: usize = 256;
const RECONNECT_INITIAL_DELAY: Duration = Duration::from_millis(500);
const RECONNECT_MAX_DELAY: Duration = Duration::from_secs(8);
const SPOOL_MAGIC: &[u8; 8] = b"MCSPCM1\0";
const SPOOL_RECORD_HEADER_SIZE: usize = 8 + 8 + 4 + 1 + 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AudioFlow {
    Microphone,
    RenderLoopback,
}

impl AudioFlow {
    pub fn data_flow(self) -> EDataFlow {
        match self {
            Self::Microphone => eCapture,
            Self::RenderLoopback => eRender,
        }
    }

    pub fn track_id(self) -> &'static str {
        match self {
            Self::Microphone => "microphone",
            Self::RenderLoopback => "system_audio",
        }
    }

    fn track_code(self) -> u8 {
        match self {
            Self::Microphone => 1,
            Self::RenderLoopback => 2,
        }
    }

    fn audio_source(self) -> &'static str {
        match self {
            Self::Microphone => "tauri_native_mic",
            Self::RenderLoopback => "tauri_system_audio",
        }
    }
}

impl fmt::Display for AudioFlow {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Microphone => "microphone",
            Self::RenderLoopback => "render_loopback",
        })
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct WindowsAudioDevice {
    /// Opaque endpoint ID used only for the current user's explicit selection.
    pub endpoint_id: String,
    pub flow: AudioFlow,
    pub state: &'static str,
    pub is_default: bool,
    pub display_name: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct DeviceEnumerationResponse {
    pub flow: AudioFlow,
    pub devices: Vec<WindowsAudioDevice>,
    pub default_endpoint_id: Option<String>,
    pub safe_to_capture: bool,
    pub safe_to_write_pcm: bool,
    pub raw_audio_uploaded: bool,
}

#[derive(Debug, Clone, Copy, Default, Serialize)]
pub struct WindowsCaptureReadiness {
    pub transport_ready: bool,
    pub pcm_seen: bool,
    pub audible_pcm_seen: bool,
    pub first_pcm_rms: Option<f32>,
    pub pcm_bytes_sent: u64,
    pub buffered_frame_count: u64,
    pub backfilled_frame_count: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct WindowsCaptureSnapshot {
    pub flow: AudioFlow,
    pub session_id: Option<String>,
    pub capture_epoch: Option<u64>,
    pub endpoint_id: Option<String>,
    pub status: &'static str,
    pub health_status: &'static str,
    pub readiness: WindowsCaptureReadiness,
    pub events: Vec<Value>,
    pub errors: Vec<String>,
    pub raw_audio_uploaded: bool,
    pub writes_raw_audio_files: bool,
}

#[derive(Debug, Clone, Copy)]
struct WaveFormat {
    format_tag: u16,
    channels: u16,
    sample_rate_hz: u32,
    block_align: u16,
    bits_per_sample: u16,
    extensible_subformat_tag: Option<u32>,
}

impl WaveFormat {
    unsafe fn from_ptr(format: *const WAVEFORMATEX) -> Result<Self, String> {
        if format.is_null() {
            return Err("WASAPI returned an empty mix format".to_string());
        }
        let format_ptr = format;
        let format = unsafe { *format_ptr };
        let format_tag = format.wFormatTag;
        let channels = format.nChannels;
        let sample_rate_hz = format.nSamplesPerSec;
        let block_align = format.nBlockAlign;
        let bits_per_sample = format.wBitsPerSample;
        let extensible_subformat_tag = if format_tag == WAVE_FORMAT_EXTENSIBLE {
            let extensible = unsafe { *(format_ptr as *const WAVEFORMATEXTENSIBLE) };
            Some(extensible.SubFormat.data1)
        } else {
            None
        };
        let effective_tag = extensible_subformat_tag.unwrap_or(format_tag as u32);
        if channels == 0 || sample_rate_hz == 0 || block_align == 0 {
            return Err("WASAPI mix format has invalid channel or rate metadata".to_string());
        }
        if !matches!(effective_tag, 1 | 3) {
            return Err(format!(
                "WASAPI mix format tag {effective_tag} is unsupported"
            ));
        }
        if (effective_tag == 3 && bits_per_sample != 32)
            || (effective_tag == 1 && !matches!(bits_per_sample, 16 | 24 | 32))
        {
            return Err(format!(
                "WASAPI mix format uses unsupported {bits_per_sample}-bit samples"
            ));
        }
        Ok(Self {
            format_tag,
            channels,
            sample_rate_hz,
            block_align,
            bits_per_sample,
            extensible_subformat_tag,
        })
    }

    fn effective_tag(self) -> u32 {
        self.extensible_subformat_tag
            .unwrap_or(self.format_tag as u32)
    }

    unsafe fn decode_mono(self, data: *const u8, frame_count: u32, silent: bool) -> Vec<f32> {
        if silent || data.is_null() {
            return vec![0.0; frame_count as usize];
        }
        let bytes = unsafe {
            std::slice::from_raw_parts(data, frame_count as usize * self.block_align as usize)
        };
        let sample_bytes = (self.bits_per_sample / 8) as usize;
        let channels = self.channels as usize;
        let mut output = Vec::with_capacity(frame_count as usize);
        for frame in bytes.chunks_exact(self.block_align as usize) {
            let mut sum = 0.0_f32;
            for channel in 0..channels {
                let offset = channel * sample_bytes;
                let sample = match (self.effective_tag(), self.bits_per_sample) {
                    (3, 32) => f32::from_le_bytes(frame[offset..offset + 4].try_into().unwrap()),
                    (1, 16) => {
                        i16::from_le_bytes(frame[offset..offset + 2].try_into().unwrap()) as f32
                            / i16::MAX as f32
                    }
                    (1, 24) => {
                        let raw = (frame[offset] as i32)
                            | ((frame[offset + 1] as i32) << 8)
                            | ((frame[offset + 2] as i32) << 16);
                        let signed = if raw & 0x0080_0000 != 0 {
                            raw | !0x00ff_ffff
                        } else {
                            raw
                        };
                        signed as f32 / 8_388_607.0
                    }
                    (1, 32) => {
                        i32::from_le_bytes(frame[offset..offset + 4].try_into().unwrap()) as f32
                            / i32::MAX as f32
                    }
                    _ => 0.0,
                };
                sum += if sample.is_finite() {
                    sample.clamp(-1.0, 1.0)
                } else {
                    0.0
                };
            }
            output.push(sum / channels as f32);
        }
        output
    }
}

struct StreamingMonoResampler {
    source_rate_hz: u32,
    input: Vec<f32>,
    input_start_index: i64,
    input_sample_count: u64,
    next_output_index: u64,
}

impl StreamingMonoResampler {
    fn new(source_rate_hz: u32) -> Result<Self, String> {
        if source_rate_hz == 0 {
            return Err("source sample rate must be greater than zero".to_string());
        }
        Ok(Self {
            source_rate_hz,
            input: Vec::new(),
            input_start_index: 0,
            input_sample_count: 0,
            next_output_index: 0,
        })
    }

    fn push(&mut self, input: &[f32]) -> Vec<f32> {
        if self.source_rate_hz == NATIVE_SAMPLE_RATE_HZ {
            return input.to_vec();
        }
        self.input.extend_from_slice(input);
        self.input_sample_count = self.input_sample_count.saturating_add(input.len() as u64);
        self.process(false)
    }

    fn finish(&mut self) -> Vec<f32> {
        if self.source_rate_hz == NATIVE_SAMPLE_RATE_HZ {
            return Vec::new();
        }
        self.process(true)
    }

    fn process(&mut self, flushing: bool) -> Vec<f32> {
        const HALF_TAPS: i64 = 32;
        let mut output = Vec::new();
        let last_available_index = self.input_start_index + self.input.len() as i64 - 1;
        loop {
            let source_position = self.next_output_index as f64 * self.source_rate_hz as f64
                / NATIVE_SAMPLE_RATE_HZ as f64;
            if flushing {
                if source_position >= self.input_sample_count as f64 {
                    break;
                }
            } else if source_position.ceil() as i64 + HALF_TAPS > last_available_index {
                break;
            }
            output.push(self.filtered_sample(source_position, HALF_TAPS));
            self.next_output_index = self.next_output_index.saturating_add(1);
        }

        let next_source_position = self.next_output_index as f64 * self.source_rate_hz as f64
            / NATIVE_SAMPLE_RATE_HZ as f64;
        let discard_before = next_source_position.floor() as i64 - HALF_TAPS - 1;
        let discard_count =
            (discard_before - self.input_start_index).clamp(0, self.input.len() as i64) as usize;
        if discard_count > 0 {
            self.input.drain(..discard_count);
            self.input_start_index += discard_count as i64;
        }
        output
    }

    fn filtered_sample(&self, source_position: f64, half_taps: i64) -> f32 {
        let cutoff = 0.45 * (NATIVE_SAMPLE_RATE_HZ as f64 / self.source_rate_hz as f64).min(1.0);
        let center = source_position.floor() as i64;
        let mut weighted_sum = 0.0_f64;
        let mut weight_sum = 0.0_f64;
        for sample_index in (center - half_taps + 1)..=(center + half_taps) {
            let distance = source_position - sample_index as f64;
            let normalized_distance = distance.abs() / half_taps as f64;
            if normalized_distance >= 1.0 {
                continue;
            }
            let sinc_argument = 2.0 * cutoff * distance;
            let sinc = if sinc_argument.abs() < f64::EPSILON {
                1.0
            } else {
                (std::f64::consts::PI * sinc_argument).sin()
                    / (std::f64::consts::PI * sinc_argument)
            };
            let window = 0.42
                + 0.5 * (std::f64::consts::PI * normalized_distance).cos()
                + 0.08 * (std::f64::consts::TAU * normalized_distance).cos();
            let weight = 2.0 * cutoff * sinc * window;
            let input_offset = sample_index - self.input_start_index;
            let sample = if input_offset >= 0 && (input_offset as usize) < self.input.len() {
                self.input[input_offset as usize] as f64
            } else {
                0.0
            };
            weighted_sum += sample * weight;
            weight_sum += weight;
        }
        if weight_sum.abs() < f64::EPSILON {
            0.0
        } else {
            (weighted_sum / weight_sum).clamp(-1.0, 1.0) as f32
        }
    }
}

fn encode_native_pcm_v2_frame(
    flow: AudioFlow,
    capture_epoch: u64,
    sequence: u64,
    timestamp_ms: u64,
    pcm: &[f32],
    final_partial: bool,
) -> Result<Vec<u8>, String> {
    if capture_epoch == 0 || sequence == 0 {
        return Err("native_pcm_v2 epoch and sequence must be positive".to_string());
    }
    if pcm.is_empty()
        || pcm.len() > NATIVE_FRAME_SAMPLES
        || (!final_partial && pcm.len() != NATIVE_FRAME_SAMPLES)
        || pcm.iter().any(|sample| !sample.is_finite())
    {
        return Err("native_pcm_v2 PCM frame length or sample is invalid".to_string());
    }
    let payload_bytes = pcm.len() * size_of::<f32>();
    let mut frame = Vec::with_capacity(NATIVE_PCM_HEADER_SIZE + payload_bytes);
    frame.extend_from_slice(b"MCPCM2\0\0");
    frame.push(2);
    frame.push(flow.track_code());
    frame.extend_from_slice(&(u16::from(final_partial)).to_be_bytes());
    frame.extend_from_slice(&capture_epoch.to_be_bytes());
    frame.extend_from_slice(&sequence.to_be_bytes());
    frame.extend_from_slice(&timestamp_ms.to_be_bytes());
    frame.extend_from_slice(&NATIVE_SAMPLE_RATE_HZ.to_be_bytes());
    frame.extend_from_slice(&(payload_bytes as u32).to_be_bytes());
    for sample in pcm {
        frame.extend_from_slice(&sample.to_le_bytes());
    }
    Ok(frame)
}

struct ComApartment {
    initialized: bool,
}

impl ComApartment {
    fn initialize() -> Result<Self, String> {
        let result = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
        Self::from_initialization_result(result)
    }

    fn from_initialization_result(result: HRESULT) -> Result<Self, String> {
        if result.is_ok() {
            return Ok(Self { initialized: true });
        }
        if result == RPC_E_CHANGED_MODE {
            // Tauri's UI thread may already own an STA apartment. MMDevice
            // enumeration is valid there; only skip the unmatched uninitialize.
            return Ok(Self { initialized: false });
        }
        Err(format!(
            "Windows COM initialization failed: {}",
            result.message()
        ))
    }
}

impl Drop for ComApartment {
    fn drop(&mut self) {
        if self.initialized {
            unsafe { windows::Win32::System::Com::CoUninitialize() };
        }
    }
}

struct WasapiCapture {
    _com: ComApartment,
    audio_client: IAudioClient,
    capture_client: IAudioCaptureClient,
    format: WaveFormat,
    resampler: StreamingMonoResampler,
    endpoint_id: String,
}

impl WasapiCapture {
    fn open(flow: AudioFlow, requested_endpoint_id: Option<&str>) -> Result<Self, String> {
        let com = ComApartment::initialize()?;
        let enumerator = endpoint_enumerator()?;
        let device = select_endpoint(&enumerator, flow, requested_endpoint_id)?;
        validate_active_state(
            unsafe { device.GetState() }
                .map_err(|error| format!("Windows {flow} endpoint state query failed: {error}"))?,
        )?;
        let selected_endpoint_id = endpoint_id(&device)?;
        let audio_client: IAudioClient = unsafe { device.Activate(CLSCTX_ALL, None) }
            .map_err(|error| format!("Windows {flow} WASAPI client activation failed: {error}"))?;
        let mix_format = unsafe { audio_client.GetMixFormat() }
            .map_err(|error| format!("Windows {flow} mix format query failed: {error}"))?;
        let format = unsafe { WaveFormat::from_ptr(mix_format) };
        let format = match format {
            Ok(value) => value,
            Err(error) => {
                unsafe { CoTaskMemFree(Some(mix_format.cast())) };
                return Err(error);
            }
        };
        let stream_flags = if flow == AudioFlow::RenderLoopback {
            AUDCLNT_STREAMFLAGS_LOOPBACK
        } else {
            0
        };
        let initialization = unsafe {
            audio_client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                stream_flags,
                10_000_000,
                0,
                mix_format,
                None,
            )
        };
        unsafe { CoTaskMemFree(Some(mix_format.cast())) };
        initialization.map_err(|error| {
            format!("Windows {flow} WASAPI shared capture initialization failed: {error}")
        })?;
        let capture_client: IAudioCaptureClient = unsafe { audio_client.GetService() }
            .map_err(|error| format!("Windows {flow} capture service is unavailable: {error}"))?;
        unsafe { audio_client.Start() }
            .map_err(|error| format!("Windows {flow} capture start failed: {error}"))?;
        let resampler = StreamingMonoResampler::new(format.sample_rate_hz)?;
        Ok(Self {
            _com: com,
            audio_client,
            capture_client,
            format,
            resampler,
            endpoint_id: selected_endpoint_id,
        })
    }

    fn poll(&mut self) -> Result<Vec<f32>, String> {
        let mut output = Vec::new();
        loop {
            let packet_frames = unsafe { self.capture_client.GetNextPacketSize() }
                .map_err(|error| format!("WASAPI packet size query failed: {error}"))?;
            if packet_frames == 0 {
                break;
            }
            let mut data = std::ptr::null_mut();
            let mut frame_count = 0_u32;
            let mut flags = 0_u32;
            unsafe {
                self.capture_client
                    .GetBuffer(&mut data, &mut frame_count, &mut flags, None, None)
            }
            .map_err(|error| format!("WASAPI capture buffer query failed: {error}"))?;
            let silent = flags & AUDCLNT_BUFFERFLAGS_SILENT.0 as u32 != 0;
            let decoded = unsafe { self.format.decode_mono(data, frame_count, silent) };
            let release = unsafe { self.capture_client.ReleaseBuffer(frame_count) };
            release.map_err(|error| format!("WASAPI capture buffer release failed: {error}"))?;
            output.extend(self.resampler.push(&decoded));
        }
        Ok(output)
    }

    fn finish(&mut self) -> Vec<f32> {
        self.resampler.finish()
    }
}

impl Drop for WasapiCapture {
    fn drop(&mut self) {
        let _ = unsafe { self.audio_client.Stop() };
    }
}

fn select_endpoint(
    enumerator: &IMMDeviceEnumerator,
    flow: AudioFlow,
    requested_endpoint_id: Option<&str>,
) -> Result<IMMDevice, String> {
    if let Some(requested) = requested_endpoint_id {
        if requested.trim().is_empty() || requested.len() > 2_048 {
            return Err("Windows audio endpoint ID is invalid".to_string());
        }
        return unsafe { enumerator.GetDevice(&HSTRING::from(requested)) }
            .map_err(|error| format!("selected Windows {flow} endpoint is unavailable: {error}"));
    }
    default_endpoint(enumerator, flow)
        .map_err(|error| format!("default Windows {flow} endpoint is unavailable: {error}"))
}

fn default_endpoint(
    enumerator: &IMMDeviceEnumerator,
    flow: AudioFlow,
) -> Result<IMMDevice, windows::core::Error> {
    match flow {
        AudioFlow::Microphone => {
            unsafe { enumerator.GetDefaultAudioEndpoint(flow.data_flow(), eCommunications) }
                .or_else(|_| unsafe {
                    enumerator.GetDefaultAudioEndpoint(flow.data_flow(), eConsole)
                })
        }
        AudioFlow::RenderLoopback => {
            unsafe { enumerator.GetDefaultAudioEndpoint(flow.data_flow(), eConsole) }.or_else(
                |_| unsafe {
                    enumerator.GetDefaultAudioEndpoint(flow.data_flow(), eCommunications)
                },
            )
        }
    }
}

fn endpoint_id(device: &windows::Win32::Media::Audio::IMMDevice) -> Result<String, String> {
    let id: PWSTR = unsafe { device.GetId() }
        .map_err(|error| format!("Windows audio endpoint ID query failed: {error}"))?;
    if id.is_null() {
        return Err("Windows audio endpoint returned an empty ID".to_string());
    }
    let value = unsafe { id.to_string() };
    unsafe { CoTaskMemFree(Some(id.as_ptr() as _)) };
    let value =
        value.map_err(|error| format!("Windows audio endpoint ID is not valid UTF-16: {error}"))?;
    if value.trim().is_empty() {
        return Err("Windows audio endpoint returned an empty ID".to_string());
    }
    Ok(value)
}

#[cfg(test)]
fn endpoint_evidence_id(endpoint_id: &str) -> String {
    let digest = Sha256::digest(endpoint_id.as_bytes());
    format!("endpoint:{}", hex::encode(&digest[..6]))
}

fn endpoint_display_name(
    device: &windows::Win32::Media::Audio::IMMDevice,
) -> Result<String, String> {
    let store = unsafe { device.OpenPropertyStore(STGM_READ) }
        .map_err(|error| format!("Windows audio endpoint property store failed: {error}"))?;
    let value = unsafe { store.GetValue(&PKEY_Device_FriendlyName) }
        .map_err(|error| format!("Windows audio endpoint name query failed: {error}"))?;
    let display_name = value.to_string();
    if display_name.trim().is_empty() {
        return Err("Windows audio endpoint returned an empty display name".to_string());
    }
    Ok(display_name)
}

fn endpoint_enumerator() -> Result<IMMDeviceEnumerator, String> {
    unsafe { CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL) }
        .map_err(|error| format!("Windows audio device enumerator is unavailable: {error}"))
}

pub fn enumerate_devices(flow: AudioFlow) -> Result<DeviceEnumerationResponse, String> {
    let _com = ComApartment::initialize()?;
    let enumerator = endpoint_enumerator()?;
    let collection =
        unsafe { enumerator.EnumAudioEndpoints(flow.data_flow(), DEVICE_STATE_ACTIVE) }
            .map_err(|error| format!("Windows {flow} endpoint enumeration failed: {error}"))?;
    let count = unsafe { collection.GetCount() }
        .map_err(|error| format!("Windows {flow} endpoint count failed: {error}"))?;
    let default_id = default_endpoint(&enumerator, flow)
        .ok()
        .and_then(|device| endpoint_id(&device).ok());

    let mut devices = Vec::with_capacity(count as usize);
    for index in 0..count {
        let device = unsafe { collection.Item(index) }
            .map_err(|error| format!("Windows {flow} endpoint {index} query failed: {error}"))?;
        let id = endpoint_id(&device)?;
        let is_default = default_id.as_deref() == Some(id.as_str());
        devices.push(WindowsAudioDevice {
            display_name: endpoint_display_name(&device)?,
            endpoint_id: id,
            flow,
            state: "active",
            is_default,
        });
    }

    if devices.is_empty() {
        return Err(ERROR_NO_AUDIO_ENDPOINTS.to_string());
    }
    Ok(DeviceEnumerationResponse {
        flow,
        default_endpoint_id: default_id,
        devices,
        safe_to_capture: false,
        safe_to_write_pcm: false,
        raw_audio_uploaded: false,
    })
}

#[derive(Debug, Clone, Serialize)]
pub struct WindowsAudioProbe {
    pub flow: AudioFlow,
    pub endpoint_id: String,
    pub duration_ms: u64,
    pub sample_count: usize,
    pub rms: f32,
    pub audible_pcm_seen: bool,
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub raw_audio_written: bool,
}

pub fn probe_device(
    flow: AudioFlow,
    endpoint_id: Option<&str>,
    duration: Duration,
) -> Result<WindowsAudioProbe, String> {
    if duration.is_zero() || duration > Duration::from_secs(10) {
        return Err("Windows audio probe duration must be between 1 ms and 10 seconds".to_string());
    }
    let mut capture = WasapiCapture::open(flow, endpoint_id)?;
    let selected_endpoint_id = capture.endpoint_id.clone();
    let source_channels = capture.format.channels;
    let started = Instant::now();
    let mut square_sum = 0.0_f64;
    let mut sample_count = 0_usize;
    while started.elapsed() < duration {
        for sample in capture.poll()? {
            square_sum += (sample as f64) * (sample as f64);
            sample_count += 1;
        }
        thread::sleep(Duration::from_millis(5));
    }
    let rms = if sample_count == 0 {
        0.0
    } else {
        (square_sum / sample_count as f64).sqrt() as f32
    };
    Ok(WindowsAudioProbe {
        flow,
        endpoint_id: selected_endpoint_id,
        duration_ms: started.elapsed().as_millis() as u64,
        sample_count,
        rms,
        audible_pcm_seen: rms >= 0.002,
        sample_rate_hz: NATIVE_SAMPLE_RATE_HZ,
        channels: source_channels,
        raw_audio_written: false,
    })
}

#[derive(Default)]
struct SharedCaptureState {
    readiness: WindowsCaptureReadiness,
    events: VecDeque<Value>,
    errors: Vec<String>,
    capture_epoch: u64,
}

struct ActiveCapture {
    stop: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
    shared: Arc<Mutex<SharedCaptureState>>,
    worker: JoinHandle<()>,
}

struct TrackCaptureState {
    active: Option<ActiveCapture>,
    last_session_id: Option<String>,
    last_capture_epoch: Option<u64>,
    last_endpoint_id: Option<String>,
    next_capture_epoch: u64,
    status: &'static str,
    last_readiness: WindowsCaptureReadiness,
    last_errors: Vec<String>,
}

impl Default for TrackCaptureState {
    fn default() -> Self {
        Self {
            active: None,
            last_session_id: None,
            last_capture_epoch: None,
            last_endpoint_id: None,
            next_capture_epoch: 0,
            status: "not_started",
            last_readiness: WindowsCaptureReadiness::default(),
            last_errors: Vec::new(),
        }
    }
}

pub struct WindowsAudioCaptureSupervisor {
    microphone: Mutex<TrackCaptureState>,
    system_audio: Mutex<TrackCaptureState>,
    spool_root: PathBuf,
}

impl Default for WindowsAudioCaptureSupervisor {
    fn default() -> Self {
        Self::new(std::env::temp_dir().join("meeting-copilot-windows-audio-spool"))
    }
}

impl WindowsAudioCaptureSupervisor {
    pub fn new(spool_root: PathBuf) -> Self {
        Self {
            microphone: Mutex::new(TrackCaptureState::default()),
            system_audio: Mutex::new(TrackCaptureState::default()),
            spool_root,
        }
    }

    fn state(&self, flow: AudioFlow) -> &Mutex<TrackCaptureState> {
        match flow {
            AudioFlow::Microphone => &self.microphone,
            AudioFlow::RenderLoopback => &self.system_audio,
        }
    }

    pub fn start(
        &self,
        flow: AudioFlow,
        session_id: Option<String>,
        requested_capture_epoch: Option<u64>,
        endpoint_id: Option<String>,
        backend: &BackendSupervisor,
    ) -> WindowsCaptureSnapshot {
        let session_id = match normalize_session_id(session_id) {
            Ok(value) => value,
            Err(error) => return error_snapshot(flow, None, None, endpoint_id, error),
        };
        let mut state = match self.state(flow).lock() {
            Ok(value) => value,
            Err(_) => {
                return error_snapshot(
                    flow,
                    Some(session_id),
                    None,
                    endpoint_id,
                    "Windows audio capture state lock poisoned".to_string(),
                )
            }
        };
        refresh_finished_capture(&mut state);
        if state.active.is_some() {
            let mut snapshot = track_snapshot(flow, &mut state, false);
            snapshot.status = "already_recording";
            snapshot.errors = vec![format!("only one Windows {flow} capture is supported")];
            return snapshot;
        }
        let capture_epoch = match requested_capture_epoch {
            Some(0) => {
                return error_snapshot(
                    flow,
                    Some(session_id),
                    None,
                    endpoint_id,
                    "capture_epoch must be greater than zero".to_string(),
                )
            }
            Some(value) => {
                state.next_capture_epoch = state.next_capture_epoch.max(value);
                value
            }
            None => match state.next_capture_epoch.checked_add(1) {
                Some(value) => {
                    state.next_capture_epoch = value;
                    value
                }
                None => {
                    return error_snapshot(
                        flow,
                        Some(session_id),
                        None,
                        endpoint_id,
                        "Windows audio capture epoch exhausted".to_string(),
                    )
                }
            },
        };
        let connection = match native_capture_connection(backend, flow, &session_id, capture_epoch)
        {
            Ok(value) => value,
            Err(error) => {
                return error_snapshot(
                    flow,
                    Some(session_id),
                    Some(capture_epoch),
                    endpoint_id,
                    error,
                )
            }
        };
        let selected_endpoint_id = match resolve_endpoint_id(flow, endpoint_id.as_deref()) {
            Ok(value) => value,
            Err(error) => {
                return error_snapshot(
                    flow,
                    Some(session_id),
                    Some(capture_epoch),
                    endpoint_id,
                    error,
                )
            }
        };
        let stop = Arc::new(AtomicBool::new(false));
        let paused = Arc::new(AtomicBool::new(false));
        let shared = Arc::new(Mutex::new(SharedCaptureState {
            capture_epoch,
            ..SharedCaptureState::default()
        }));
        let (startup_tx, startup_rx) = mpsc::sync_channel(1);
        let worker_stop = Arc::clone(&stop);
        let worker_paused = Arc::clone(&paused);
        let worker_shared = Arc::clone(&shared);
        let worker_endpoint = selected_endpoint_id.clone();
        let worker_session_id = session_id.clone();
        let worker_spool_root = self.spool_root.clone();
        let worker = thread::spawn(move || {
            if let Err(error) = capture_and_stream(
                flow,
                &worker_endpoint,
                &worker_session_id,
                capture_epoch,
                connection,
                &worker_spool_root,
                worker_stop,
                worker_paused,
                Arc::clone(&worker_shared),
                startup_tx,
            ) {
                record_capture_error(&worker_shared, error);
            }
        });
        match startup_rx.recv_timeout(START_TIMEOUT) {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                stop.store(true, Ordering::Release);
                let _ = worker.join();
                return error_snapshot(
                    flow,
                    Some(session_id),
                    Some(capture_epoch),
                    Some(selected_endpoint_id),
                    error,
                );
            }
            Err(_) => {
                stop.store(true, Ordering::Release);
                let _ = worker.join();
                return error_snapshot(
                    flow,
                    Some(session_id),
                    Some(capture_epoch),
                    Some(selected_endpoint_id),
                    "Windows audio capture startup timed out".to_string(),
                );
            }
        }
        state.last_session_id = Some(session_id.clone());
        state.last_capture_epoch = Some(capture_epoch);
        state.last_endpoint_id = Some(selected_endpoint_id.clone());
        state.status = "recording";
        state.last_errors.clear();
        state.last_readiness = WindowsCaptureReadiness::default();
        state.active = Some(ActiveCapture {
            stop,
            paused,
            shared,
            worker,
        });
        track_snapshot(flow, &mut state, false)
    }

    pub fn status(&self, flow: AudioFlow) -> WindowsCaptureSnapshot {
        let mut state = match self.state(flow).lock() {
            Ok(value) => value,
            Err(_) => {
                return error_snapshot(
                    flow,
                    None,
                    None,
                    None,
                    "Windows audio capture state lock poisoned".to_string(),
                )
            }
        };
        refresh_finished_capture(&mut state);
        track_snapshot(flow, &mut state, false)
    }

    pub fn collect_events(
        &self,
        flow: AudioFlow,
        session_id: Option<String>,
    ) -> WindowsCaptureSnapshot {
        let requested = match normalize_session_id(session_id) {
            Ok(value) => value,
            Err(error) => return error_snapshot(flow, None, None, None, error),
        };
        let mut state = match self.state(flow).lock() {
            Ok(value) => value,
            Err(_) => {
                return error_snapshot(
                    flow,
                    Some(requested),
                    None,
                    None,
                    "Windows audio capture state lock poisoned".to_string(),
                )
            }
        };
        refresh_finished_capture(&mut state);
        if state.last_session_id.as_deref() != Some(requested.as_str()) {
            return error_snapshot(
                flow,
                Some(requested),
                None,
                None,
                "Windows audio session does not match the active session".to_string(),
            );
        }
        track_snapshot(flow, &mut state, true)
    }

    pub fn stop(
        &self,
        flow: AudioFlow,
        requested_session_id: Option<&str>,
    ) -> WindowsCaptureSnapshot {
        let mut state = match self.state(flow).lock() {
            Ok(value) => value,
            Err(_) => {
                return error_snapshot(
                    flow,
                    requested_session_id.map(ToOwned::to_owned),
                    None,
                    None,
                    "Windows audio capture state lock poisoned".to_string(),
                )
            }
        };
        refresh_finished_capture(&mut state);
        if let Some(requested) = requested_session_id {
            if state.active.is_some() && state.last_session_id.as_deref() != Some(requested) {
                let mut snapshot = track_snapshot(flow, &mut state, false);
                snapshot.status = "conflict";
                snapshot.errors =
                    vec!["Windows audio session does not match the active session".to_string()];
                return snapshot;
            }
        }
        let Some(active) = state.active.take() else {
            state.status = "stopped";
            return track_snapshot(flow, &mut state, false);
        };
        active.stop.store(true, Ordering::Release);
        let deadline = Instant::now() + STOP_TIMEOUT;
        while !active.worker.is_finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        if !active.worker.is_finished() {
            state.active = Some(active);
            let mut snapshot = track_snapshot(flow, &mut state, false);
            snapshot.status = "stop_timeout";
            snapshot
                .errors
                .push("Windows audio capture did not stop within the bounded timeout".to_string());
            return snapshot;
        }
        let _ = active.worker.join();
        copy_shared_state(&active.shared, &mut state, false);
        state.status = "stopped";
        track_snapshot(flow, &mut state, false)
    }

    pub fn pause(&self, flow: AudioFlow) -> WindowsCaptureSnapshot {
        let mut state = self.state(flow).lock().unwrap();
        refresh_finished_capture(&mut state);
        let Some(active) = state.active.as_ref() else {
            return error_snapshot(
                flow,
                state.last_session_id.clone(),
                state.last_capture_epoch,
                state.last_endpoint_id.clone(),
                "Windows audio capture is not recording".to_string(),
            );
        };
        active.paused.store(true, Ordering::Release);
        state.status = "paused";
        track_snapshot(flow, &mut state, false)
    }

    pub fn resume(&self, flow: AudioFlow) -> WindowsCaptureSnapshot {
        let mut state = self.state(flow).lock().unwrap();
        refresh_finished_capture(&mut state);
        let Some(active) = state.active.as_ref() else {
            return error_snapshot(
                flow,
                state.last_session_id.clone(),
                state.last_capture_epoch,
                state.last_endpoint_id.clone(),
                "Windows audio capture is not paused".to_string(),
            );
        };
        active.paused.store(false, Ordering::Release);
        state.status = "recording";
        track_snapshot(flow, &mut state, false)
    }

    pub fn cleanup(
        &self,
        flow: AudioFlow,
        requested_session_id: Option<&str>,
    ) -> WindowsCaptureSnapshot {
        let stopped = self.stop(flow, requested_session_id);
        if stopped.status == "conflict" || stopped.status == "stop_timeout" {
            return stopped;
        }
        let mut state = self.state(flow).lock().unwrap();
        state.status = "cleaned";
        state.last_readiness = WindowsCaptureReadiness::default();
        state.last_errors.clear();
        track_snapshot(flow, &mut state, false)
    }
}

impl Drop for WindowsAudioCaptureSupervisor {
    fn drop(&mut self) {
        let _ = self.stop(AudioFlow::Microphone, None);
        let _ = self.stop(AudioFlow::RenderLoopback, None);
    }
}

fn resolve_endpoint_id(flow: AudioFlow, requested: Option<&str>) -> Result<String, String> {
    let _com = ComApartment::initialize()?;
    let enumerator = endpoint_enumerator()?;
    let device = select_endpoint(&enumerator, flow, requested)?;
    validate_active_state(
        unsafe { device.GetState() }
            .map_err(|error| format!("Windows {flow} endpoint state query failed: {error}"))?,
    )?;
    endpoint_id(&device)
}

fn native_capture_connection(
    backend: &BackendSupervisor,
    flow: AudioFlow,
    session_id: &str,
    capture_epoch: u64,
) -> Result<BackendWebSocketConnection, String> {
    if capture_epoch == 0 {
        return Err("Windows audio capture epoch must be greater than zero".to_string());
    }
    let connection = backend.native_microphone_connection(session_id)?;
    connection_for_epoch(&connection, flow, capture_epoch)
}

fn connection_for_epoch(
    template: &BackendWebSocketConnection,
    flow: AudioFlow,
    capture_epoch: u64,
) -> Result<BackendWebSocketConnection, String> {
    let mut connection = template.clone();
    let mut parsed = url::Url::parse(&connection.url)
        .map_err(|error| format!("backend websocket URL is invalid: {error}"))?;
    if parsed.scheme() != "ws"
        || parsed.host_str() != Some("127.0.0.1")
        || parsed.port().is_none()
        || !parsed.path().starts_with("/live/asr/stream/ws/")
    {
        return Err(
            "Windows audio transport is restricted to the authenticated packaged loopback backend"
                .to_string(),
        );
    }
    parsed
        .query_pairs_mut()
        .clear()
        .append_pair("audio_source", flow.audio_source())
        .append_pair("pcm_protocol", "native_pcm_v2")
        .append_pair("capture_epoch", &capture_epoch.to_string());
    connection.url = parsed.into();
    Ok(connection)
}

fn normalize_session_id(session_id: Option<String>) -> Result<String, String> {
    let value = session_id.unwrap_or_default();
    if value.is_empty()
        || value.len() > 128
        || !value.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '_' | '-' | '.')
        })
    {
        return Err("session_id contains unsafe characters".to_string());
    }
    Ok(value)
}

#[derive(Debug)]
struct SpoolFrame {
    global_sequence: u64,
    timestamp_ms: u64,
    samples: Vec<f32>,
    final_partial: bool,
}

struct DurablePcmSpool {
    path: PathBuf,
    file: File,
}

impl DurablePcmSpool {
    fn create(
        root: &Path,
        session_id: &str,
        flow: AudioFlow,
        capture_epoch: u64,
    ) -> Result<Self, String> {
        let directory = root.join(flow.track_id()).join(session_id);
        ensure_private_directory(&directory)
            .map_err(|error| format!("Windows audio spool directory is unavailable: {error}"))?;
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| format!("Windows audio spool clock is invalid: {error}"))?
            .as_millis();
        let path = directory.join(format!(
            "epoch-{capture_epoch}-pid-{}-{nonce}.pcmspool",
            std::process::id()
        ));
        let mut file = open_private_file(&path, false)
            .map_err(|error| format!("Windows audio spool could not be created: {error}"))?;
        file.write_all(SPOOL_MAGIC)
            .and_then(|_| file.sync_data())
            .map_err(|error| format!("Windows audio spool header could not be saved: {error}"))?;
        Ok(Self { path, file })
    }

    fn append(&mut self, frame: &SpoolFrame) -> Result<(), String> {
        let mut pcm = Vec::with_capacity(frame.samples.len() * size_of::<f32>());
        for sample in &frame.samples {
            pcm.extend_from_slice(&sample.to_le_bytes());
        }
        let digest = Sha256::digest(&pcm);
        let sample_count = u32::try_from(frame.samples.len())
            .map_err(|_| "Windows audio spool frame is too large".to_string())?;
        let mut header = Vec::with_capacity(SPOOL_RECORD_HEADER_SIZE);
        header.extend_from_slice(&frame.global_sequence.to_be_bytes());
        header.extend_from_slice(&frame.timestamp_ms.to_be_bytes());
        header.extend_from_slice(&sample_count.to_be_bytes());
        header.push(u8::from(frame.final_partial));
        header.extend_from_slice(digest.as_slice());
        debug_assert_eq!(header.len(), SPOOL_RECORD_HEADER_SIZE);
        self.file
            .write_all(&header)
            .and_then(|_| self.file.write_all(&pcm))
            .and_then(|_| self.file.flush())
            .and_then(|_| self.file.sync_data())
            .map_err(|error| format!("Windows audio spool frame could not be saved: {error}"))
    }

    fn remove(self) -> Result<(), String> {
        let path = self.path.clone();
        drop(self.file);
        fs::remove_file(path)
            .map_err(|error| format!("Windows audio spool cleanup failed: {error}"))
    }
}

fn open_capture_socket(
    connection: &BackendWebSocketConnection,
) -> Result<WebSocket<MaybeTlsStream<std::net::TcpStream>>, String> {
    let mut request = connection
        .url
        .as_str()
        .into_client_request()
        .map_err(|error| format!("Windows audio WebSocket request is invalid: {error}"))?;
    if !connection.cookie.is_empty() {
        request.headers_mut().insert(
            COOKIE,
            HeaderValue::from_str(&connection.cookie)
                .map_err(|_| "Windows audio session cookie is invalid".to_string())?,
        );
    }
    let (mut socket, _) = connect(request)
        .map_err(|error| format!("Windows audio loopback WebSocket connection failed: {error}"))?;
    if let MaybeTlsStream::Plain(stream) = socket.get_mut() {
        let _ = stream.set_read_timeout(Some(Duration::from_millis(2)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
    }
    Ok(socket)
}

fn capture_and_stream(
    flow: AudioFlow,
    endpoint_id: &str,
    session_id: &str,
    capture_epoch: u64,
    connection: BackendWebSocketConnection,
    spool_root: &Path,
    stop: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
    shared: Arc<Mutex<SharedCaptureState>>,
    startup: mpsc::SyncSender<Result<(), String>>,
) -> Result<(), String> {
    let mut socket = Some(open_capture_socket(&connection)?);
    let mut capture = match WasapiCapture::open(flow, Some(endpoint_id)) {
        Ok(value) => value,
        Err(error) => {
            let _ = startup.send(Err(error.clone()));
            return Err(error);
        }
    };
    if let Ok(mut state) = shared.lock() {
        state.readiness.transport_ready = true;
    }

    let started = Instant::now();
    let mut spool = DurablePcmSpool::create(spool_root, session_id, flow, capture_epoch)?;
    let mut pending = Vec::with_capacity(NATIVE_FRAME_SAMPLES * 2);
    let mut outbound = VecDeque::<SpoolFrame>::new();
    let mut global_sequence = 0_u64;
    let mut transport_sequence = 0_u64;
    let mut transport_epoch = capture_epoch;
    let mut reconnect_epoch = capture_epoch.saturating_add(1);
    let mut reconnect_delay = RECONNECT_INITIAL_DELAY;
    let mut reconnect_at = Instant::now();
    let mut recovery_gap_start_ms: Option<u64> = None;
    let mut startup = Some(startup);
    while !stop.load(Ordering::Acquire) {
        let samples = capture.poll()?;
        if paused.load(Ordering::Acquire) {
            pending.clear();
        } else {
            pending.extend(samples);
        }
        if flow == AudioFlow::RenderLoopback
            && pending.is_empty()
            && global_sequence == 0
            && started.elapsed() >= Duration::from_millis(300)
        {
            pending.resize(NATIVE_FRAME_SAMPLES, 0.0);
        }
        while pending.len() >= NATIVE_FRAME_SAMPLES {
            global_sequence = global_sequence
                .checked_add(1)
                .ok_or_else(|| "Windows audio spool sequence exhausted".to_string())?;
            let frame = SpoolFrame {
                global_sequence,
                timestamp_ms: started.elapsed().as_millis() as u64,
                samples: pending.drain(..NATIVE_FRAME_SAMPLES).collect(),
                final_partial: false,
            };
            spool.append(&frame)?;
            outbound.push_back(frame);
            update_buffered_frames(&shared, outbound.len());
        }

        if socket.is_none() && Instant::now() >= reconnect_at {
            let next_connection = connection_for_epoch(&connection, flow, reconnect_epoch);
            match next_connection.and_then(|candidate| open_capture_socket(&candidate)) {
                Ok(next_socket) => {
                    socket = Some(next_socket);
                    transport_epoch = reconnect_epoch;
                    reconnect_epoch = reconnect_epoch.saturating_add(1);
                    transport_sequence = 0;
                    reconnect_delay = RECONNECT_INITIAL_DELAY;
                    set_transport_state(
                        &shared,
                        transport_epoch,
                        true,
                        "backfilling",
                        recovery_gap_start_ms,
                        outbound.back().map(|frame| frame.timestamp_ms),
                    );
                }
                Err(_) => {
                    reconnect_at = Instant::now() + reconnect_delay;
                    reconnect_delay = (reconnect_delay * 2).min(RECONNECT_MAX_DELAY);
                }
            }
        }

        if let Some(active_socket) = socket.as_mut() {
            let was_backfilling = recovery_gap_start_ms.is_some();
            let flush_result = flush_spooled_frames(
                active_socket,
                flow,
                transport_epoch,
                &mut transport_sequence,
                &mut outbound,
                &shared,
                was_backfilling,
            )
            .and_then(|_| drain_websocket_events(active_socket, &shared));
            if let Err(_) = flush_result {
                socket = None;
                if recovery_gap_start_ms.is_none() {
                    recovery_gap_start_ms = outbound
                        .front()
                        .map(|frame| frame.timestamp_ms)
                        .or_else(|| Some(started.elapsed().as_millis() as u64));
                }
                reconnect_at = Instant::now() + reconnect_delay;
                set_transport_state(
                    &shared,
                    transport_epoch,
                    false,
                    "reconnecting",
                    recovery_gap_start_ms,
                    Some(started.elapsed().as_millis() as u64),
                );
            } else if outbound.is_empty() {
                if let Some(gap_start_ms) = recovery_gap_start_ms.take() {
                    set_transport_state(
                        &shared,
                        transport_epoch,
                        true,
                        "recovered",
                        Some(gap_start_ms),
                        Some(started.elapsed().as_millis() as u64),
                    );
                }
                let pcm_seen = shared
                    .lock()
                    .map(|state| state.readiness.pcm_seen)
                    .unwrap_or(false);
                if pcm_seen {
                    if let Some(sender) = startup.take() {
                        sender
                            .send(Ok(()))
                            .map_err(|_| "Windows audio startup owner disconnected".to_string())?;
                    }
                }
            }
        }
        thread::sleep(Duration::from_millis(5));
    }
    if let Some(sender) = startup.take() {
        let _ = sender.send(Err(
            "Windows audio capture stopped before the first PCM frame".to_string(),
        ));
    }
    if !paused.load(Ordering::Acquire) {
        pending.extend(capture.finish());
    }
    if !pending.is_empty() {
        global_sequence = global_sequence
            .checked_add(1)
            .ok_or_else(|| "Windows audio spool sequence exhausted".to_string())?;
        let frame = SpoolFrame {
            global_sequence,
            timestamp_ms: started.elapsed().as_millis() as u64,
            samples: pending,
            final_partial: true,
        };
        spool.append(&frame)?;
        outbound.push_back(frame);
        update_buffered_frames(&shared, outbound.len());
    }
    let Some(mut socket) = socket else {
        return Err(
            "Windows audio transport ended offline; private local spool was preserved".to_string(),
        );
    };
    flush_spooled_frames(
        &mut socket,
        flow,
        transport_epoch,
        &mut transport_sequence,
        &mut outbound,
        &shared,
        recovery_gap_start_ms.is_some(),
    )?;
    socket
        .send(Message::Text("END".into()))
        .map_err(|error| format!("Windows audio stream finalization failed: {error}"))?;
    wait_for_websocket_finalization(&mut socket, &shared)?;
    let _ = socket.close(None);
    spool.remove()?;
    Ok(())
}

fn flush_spooled_frames(
    socket: &mut WebSocket<MaybeTlsStream<std::net::TcpStream>>,
    flow: AudioFlow,
    capture_epoch: u64,
    transport_sequence: &mut u64,
    outbound: &mut VecDeque<SpoolFrame>,
    shared: &Arc<Mutex<SharedCaptureState>>,
    backfilling: bool,
) -> Result<(), String> {
    while let Some(frame) = outbound.front() {
        let next_sequence = transport_sequence
            .checked_add(1)
            .ok_or_else(|| "native_pcm_v2 sequence exhausted".to_string())?;
        send_pcm_frame(
            socket,
            flow,
            capture_epoch,
            next_sequence,
            frame.timestamp_ms,
            &frame.samples,
            frame.final_partial,
            shared,
        )?;
        *transport_sequence = next_sequence;
        outbound.pop_front();
        if let Ok(mut state) = shared.lock() {
            state.readiness.buffered_frame_count = outbound.len() as u64;
            if backfilling {
                state.readiness.backfilled_frame_count =
                    state.readiness.backfilled_frame_count.saturating_add(1);
            }
        }
    }
    Ok(())
}

fn update_buffered_frames(shared: &Arc<Mutex<SharedCaptureState>>, count: usize) {
    if let Ok(mut state) = shared.lock() {
        state.readiness.buffered_frame_count = count as u64;
    }
}

fn set_transport_state(
    shared: &Arc<Mutex<SharedCaptureState>>,
    capture_epoch: u64,
    transport_ready: bool,
    recovery_state: &'static str,
    gap_start_ms: Option<u64>,
    gap_end_ms: Option<u64>,
) {
    if let Ok(mut state) = shared.lock() {
        state.capture_epoch = capture_epoch;
        state.readiness.transport_ready = transport_ready;
        if state.events.len() == EVENT_LIMIT {
            state.events.pop_front();
        }
        let buffered_frame_count = state.readiness.buffered_frame_count;
        let backfilled_frame_count = state.readiness.backfilled_frame_count;
        state.events.push_back(json!({
            "event_type": "capture_recovery",
            "state": recovery_state,
            "capture_epoch": capture_epoch,
            "recording_continues": true,
            "buffered_frame_count": buffered_frame_count,
            "backfilled_frame_count": backfilled_frame_count,
            "gap_start_ms": gap_start_ms,
            "gap_end_ms": gap_end_ms,
        }));
    }
}

fn send_pcm_frame(
    socket: &mut WebSocket<MaybeTlsStream<std::net::TcpStream>>,
    flow: AudioFlow,
    capture_epoch: u64,
    sequence: u64,
    timestamp_ms: u64,
    pcm: &[f32],
    final_partial: bool,
    shared: &Arc<Mutex<SharedCaptureState>>,
) -> Result<(), String> {
    let frame = encode_native_pcm_v2_frame(
        flow,
        capture_epoch,
        sequence,
        timestamp_ms,
        pcm,
        final_partial,
    )?;
    socket
        .send(Message::Binary(frame.into()))
        .map_err(|error| format!("Windows audio PCM transport failed: {error}"))?;
    let square_sum: f64 = pcm
        .iter()
        .map(|sample| (*sample as f64) * (*sample as f64))
        .sum();
    let rms = (square_sum / pcm.len() as f64).sqrt() as f32;
    if let Ok(mut state) = shared.lock() {
        if !state.readiness.pcm_seen {
            state.readiness.first_pcm_rms = Some(rms);
        }
        state.readiness.pcm_seen = true;
        state.readiness.audible_pcm_seen |= rms >= 0.002;
        state.readiness.pcm_bytes_sent = state
            .readiness
            .pcm_bytes_sent
            .saturating_add((pcm.len() * size_of::<f32>()) as u64);
    }
    Ok(())
}

fn drain_websocket_events(
    socket: &mut WebSocket<MaybeTlsStream<std::net::TcpStream>>,
    shared: &Arc<Mutex<SharedCaptureState>>,
) -> Result<(), String> {
    loop {
        match socket.read() {
            Ok(Message::Text(text)) => {
                push_websocket_event(shared, &text);
            }
            Ok(Message::Ping(payload)) => {
                socket
                    .send(Message::Pong(payload))
                    .map_err(|error| format!("Windows audio WebSocket pong failed: {error}"))?;
            }
            Ok(Message::Close(_)) => {
                return Err("Windows audio backend closed the capture stream".to_string())
            }
            Ok(_) => {}
            Err(tungstenite::Error::Io(error))
                if matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) =>
            {
                return Ok(())
            }
            Err(tungstenite::Error::ConnectionClosed) => {
                return Err("Windows audio backend capture stream closed".to_string())
            }
            Err(error) => return Err(format!("Windows audio WebSocket receive failed: {error}")),
        }
    }
}

fn wait_for_websocket_finalization(
    socket: &mut WebSocket<MaybeTlsStream<std::net::TcpStream>>,
    shared: &Arc<Mutex<SharedCaptureState>>,
) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut terminal_seen_at = None;
    loop {
        match socket.read() {
            Ok(Message::Text(text)) => {
                let terminal = serde_json::from_str::<Value>(&text)
                    .ok()
                    .and_then(|value| {
                        value
                            .get("event_type")
                            .and_then(Value::as_str)
                            .map(str::to_owned)
                    })
                    .is_some_and(|event_type| {
                        matches!(event_type.as_str(), "final" | "provider_error")
                    });
                push_websocket_event(shared, &text);
                if terminal {
                    terminal_seen_at = Some(Instant::now());
                }
            }
            Ok(Message::Ping(payload)) => {
                socket
                    .send(Message::Pong(payload))
                    .map_err(|error| format!("Windows audio WebSocket pong failed: {error}"))?;
            }
            Ok(Message::Close(_)) | Err(tungstenite::Error::ConnectionClosed) => return Ok(()),
            Ok(_) => {}
            Err(tungstenite::Error::Io(error))
                if matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
            Err(error) => return Err(format!("Windows audio final response failed: {error}")),
        }
        if terminal_seen_at.is_some_and(|seen| seen.elapsed() >= Duration::from_millis(100)) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("Windows audio backend finalization timed out".to_string());
        }
    }
}

fn push_websocket_event(shared: &Arc<Mutex<SharedCaptureState>>, text: &str) {
    let Ok(value) = serde_json::from_str::<Value>(text) else {
        return;
    };
    if let Ok(mut state) = shared.lock() {
        if state.events.len() == EVENT_LIMIT {
            state.events.pop_front();
        }
        state.events.push_back(value);
    }
}

fn record_capture_error(shared: &Arc<Mutex<SharedCaptureState>>, error: String) {
    if let Ok(mut state) = shared.lock() {
        state.errors.push(error);
    }
}

fn refresh_finished_capture(state: &mut TrackCaptureState) {
    let finished = state
        .active
        .as_ref()
        .is_some_and(|active| active.worker.is_finished());
    if !finished {
        return;
    }
    let active = state.active.take().unwrap();
    let _ = active.worker.join();
    copy_shared_state(&active.shared, state, false);
    state.status = if state.last_errors.is_empty() {
        "stopped"
    } else {
        "error"
    };
}

fn copy_shared_state(
    shared: &Arc<Mutex<SharedCaptureState>>,
    state: &mut TrackCaptureState,
    drain_events: bool,
) -> Vec<Value> {
    let Ok(mut shared) = shared.lock() else {
        state.last_errors = vec!["Windows audio shared capture state lock poisoned".to_string()];
        return Vec::new();
    };
    state.last_readiness = shared.readiness;
    if shared.capture_epoch > 0 {
        state.last_capture_epoch = Some(shared.capture_epoch);
        state.next_capture_epoch = state.next_capture_epoch.max(shared.capture_epoch);
    }
    state.last_errors = shared.errors.clone();
    if drain_events {
        shared.events.drain(..).collect()
    } else {
        Vec::new()
    }
}

fn track_snapshot(
    flow: AudioFlow,
    state: &mut TrackCaptureState,
    drain_events: bool,
) -> WindowsCaptureSnapshot {
    let events = if let Some(active) = state.active.as_ref() {
        let shared = Arc::clone(&active.shared);
        copy_shared_state(&shared, state, drain_events)
    } else {
        Vec::new()
    };
    let readiness = state.last_readiness;
    let errors = state.last_errors.clone();
    let status = state.status;
    WindowsCaptureSnapshot {
        flow,
        session_id: state.last_session_id.clone(),
        capture_epoch: state.last_capture_epoch,
        endpoint_id: state.last_endpoint_id.clone(),
        status,
        health_status: if status == "recording" && errors.is_empty() {
            if !readiness.transport_ready && readiness.pcm_seen {
                "recovering"
            } else if readiness.buffered_frame_count > 0 {
                "backfilling"
            } else if readiness.pcm_seen {
                "healthy"
            } else {
                "starting"
            }
        } else if status == "error" || !errors.is_empty() {
            "error"
        } else {
            status
        },
        readiness,
        events,
        errors,
        raw_audio_uploaded: false,
        writes_raw_audio_files: matches!(status, "recording" | "paused"),
    }
}

fn error_snapshot(
    flow: AudioFlow,
    session_id: Option<String>,
    capture_epoch: Option<u64>,
    endpoint_id: Option<String>,
    error: String,
) -> WindowsCaptureSnapshot {
    WindowsCaptureSnapshot {
        flow,
        session_id,
        capture_epoch,
        endpoint_id,
        status: "error",
        health_status: "error",
        readiness: WindowsCaptureReadiness::default(),
        events: Vec::new(),
        errors: vec![error],
        raw_audio_uploaded: false,
        writes_raw_audio_files: false,
    }
}

pub fn parse_flow(value: &str) -> Result<AudioFlow, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "microphone" | "capture" => Ok(AudioFlow::Microphone),
        "system_audio" | "render_loopback" | "loopback" => Ok(AudioFlow::RenderLoopback),
        _ => Err("audio flow must be microphone or render_loopback".to_string()),
    }
}

pub fn validate_active_state(state: DEVICE_STATE) -> Result<(), String> {
    if state == DEVICE_STATE_ACTIVE {
        Ok(())
    } else {
        Err("Windows audio endpoint is not active".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hardware_smoke_capture_duration(default_seconds: u64) -> Duration {
        let seconds = std::env::var("MEETING_COPILOT_WINDOWS_AUDIO_SMOKE_CAPTURE_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| (1..=60).contains(value))
            .unwrap_or(default_seconds);
        Duration::from_secs(seconds)
    }

    #[test]
    fn flow_contract_maps_to_shared_tracks() {
        assert_eq!(AudioFlow::Microphone.track_id(), "microphone");
        assert_eq!(AudioFlow::RenderLoopback.track_id(), "system_audio");
        assert_eq!(AudioFlow::Microphone.to_string(), "microphone");
    }

    #[test]
    fn invalid_flow_is_rejected_without_touching_audio() {
        assert!(parse_flow("speaker").is_err());
        assert_eq!(parse_flow("loopback").unwrap(), AudioFlow::RenderLoopback);
    }

    #[test]
    fn inactive_endpoint_is_fail_closed() {
        assert!(validate_active_state(DEVICE_STATE(0)).is_err());
        assert!(validate_active_state(DEVICE_STATE_ACTIVE).is_ok());
    }

    #[test]
    fn existing_sta_apartment_is_accepted_without_owning_uninitialize() {
        let apartment = ComApartment::from_initialization_result(RPC_E_CHANGED_MODE).unwrap();
        assert!(!apartment.initialized);
    }

    #[test]
    fn real_windows_endpoint_enumeration_is_explicitly_non_capture() {
        for flow in [AudioFlow::Microphone, AudioFlow::RenderLoopback] {
            match enumerate_devices(flow) {
                Ok(response) => {
                    eprintln!(
                        "windows_audio_devices flow={} count={} default_present={}",
                        response.flow,
                        response.devices.len(),
                        response.default_endpoint_id.is_some()
                    );
                    assert!(!response.devices.is_empty());
                    assert!(response
                        .devices
                        .iter()
                        .all(|device| !device.display_name.trim().is_empty()));
                    assert!(!response.safe_to_capture);
                    assert!(!response.safe_to_write_pcm);
                    assert!(!response.raw_audio_uploaded);
                }
                Err(error) => assert!(
                    error.contains(ERROR_NO_AUDIO_ENDPOINTS)
                        || error.contains("enumerator")
                        || error.contains("endpoint")
                ),
            }
        }
    }

    #[test]
    fn native_pcm_v2_frames_bind_track_epoch_sequence_and_little_endian_samples() {
        let frame = encode_native_pcm_v2_frame(
            AudioFlow::Microphone,
            7,
            3,
            12_345,
            &[0.125; NATIVE_FRAME_SAMPLES],
            false,
        )
        .unwrap();

        assert_eq!(&frame[..8], b"MCPCM2\0\0");
        assert_eq!(frame[8], 2);
        assert_eq!(frame[9], 1);
        assert_eq!(u16::from_be_bytes(frame[10..12].try_into().unwrap()), 0);
        assert_eq!(u64::from_be_bytes(frame[12..20].try_into().unwrap()), 7);
        assert_eq!(u64::from_be_bytes(frame[20..28].try_into().unwrap()), 3);
        assert_eq!(
            u64::from_be_bytes(frame[28..36].try_into().unwrap()),
            12_345
        );
        assert_eq!(
            u32::from_be_bytes(frame[36..40].try_into().unwrap()),
            16_000
        );
        assert_eq!(
            u32::from_be_bytes(frame[40..44].try_into().unwrap()),
            (NATIVE_FRAME_SAMPLES * size_of::<f32>()) as u32
        );
        assert_eq!(f32::from_le_bytes(frame[44..48].try_into().unwrap()), 0.125);
    }

    #[test]
    fn native_pcm_v2_rejects_zero_identity_and_non_final_short_frames() {
        assert!(encode_native_pcm_v2_frame(
            AudioFlow::Microphone,
            0,
            1,
            0,
            &[0.0; NATIVE_FRAME_SAMPLES],
            false,
        )
        .is_err());
        assert!(encode_native_pcm_v2_frame(
            AudioFlow::Microphone,
            1,
            0,
            0,
            &[0.0; NATIVE_FRAME_SAMPLES],
            false,
        )
        .is_err());
        assert!(
            encode_native_pcm_v2_frame(AudioFlow::RenderLoopback, 1, 1, 0, &[0.0; 160], false,)
                .is_err()
        );
        assert!(
            encode_native_pcm_v2_frame(AudioFlow::RenderLoopback, 1, 1, 0, &[0.0; 160], true,)
                .is_ok()
        );
    }

    #[test]
    fn private_spool_persists_sequence_timestamp_payload_and_checksum_before_transport() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-spool-test-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let mut spool =
            DurablePcmSpool::create(&root, "meeting_spool_contract", AudioFlow::Microphone, 4)
                .unwrap();
        let frame = SpoolFrame {
            global_sequence: 9,
            timestamp_ms: 12_345,
            samples: vec![0.125, -0.25],
            final_partial: true,
        };
        spool.append(&frame).unwrap();
        let path = spool.path.clone();
        drop(spool);

        let bytes = fs::read(&path).unwrap();
        assert_eq!(&bytes[..SPOOL_MAGIC.len()], SPOOL_MAGIC);
        let header = &bytes[SPOOL_MAGIC.len()..SPOOL_MAGIC.len() + SPOOL_RECORD_HEADER_SIZE];
        assert_eq!(u64::from_be_bytes(header[0..8].try_into().unwrap()), 9);
        assert_eq!(
            u64::from_be_bytes(header[8..16].try_into().unwrap()),
            12_345
        );
        assert_eq!(u32::from_be_bytes(header[16..20].try_into().unwrap()), 2);
        assert_eq!(header[20], 1);
        let payload = &bytes[SPOOL_MAGIC.len() + SPOOL_RECORD_HEADER_SIZE..];
        assert_eq!(payload.len(), 2 * size_of::<f32>());
        assert_eq!(&header[21..53], Sha256::digest(payload).as_slice());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn reconnect_connection_advances_epoch_without_changing_private_identity() {
        let template = BackendWebSocketConnection {
            url: "ws://127.0.0.1:45678/live/asr/stream/ws/meeting_resume?audio_source=tauri_native_mic&pcm_protocol=native_pcm_v2&capture_epoch=1".to_string(),
            cookie: "meeting_copilot_session=private".to_string(),
        };
        let resumed = connection_for_epoch(&template, AudioFlow::Microphone, 7).unwrap();
        assert!(resumed.url.contains("audio_source=tauri_native_mic"));
        assert!(resumed.url.contains("pcm_protocol=native_pcm_v2"));
        assert!(resumed.url.contains("capture_epoch=7"));
        assert_eq!(resumed.cookie, template.cookie);
    }

    #[test]
    fn streaming_resampler_produces_exact_sixteen_kilohertz_rate() {
        let mut resampler = StreamingMonoResampler::new(48_000).unwrap();
        let source: Vec<f32> = (0..48_000)
            .map(|index| if index % 2 == 0 { 0.25 } else { -0.25 })
            .collect();
        let mut output = resampler.push(&source);
        output.extend(resampler.finish());

        assert_eq!(output.len(), 16_000);
        assert!(output.iter().all(|sample| sample.is_finite()));
    }

    #[test]
    fn streaming_resampler_attenuates_content_above_target_nyquist() {
        let mut resampler = StreamingMonoResampler::new(48_000).unwrap();
        let source: Vec<f32> = (0..48_000)
            .map(|index| {
                let phase = std::f32::consts::TAU * 12_000.0 * index as f32 / 48_000.0;
                phase.sin()
            })
            .collect();
        let mut output = resampler.push(&source);
        output.extend(resampler.finish());
        let output_rms = (output
            .iter()
            .map(|sample| (*sample as f64) * (*sample as f64))
            .sum::<f64>()
            / output.len() as f64)
            .sqrt();

        assert_eq!(output.len(), 16_000);
        assert!(
            output_rms < 0.02,
            "above-Nyquist signal aliased into speech band with RMS {output_rms}"
        );
    }

    #[test]
    #[ignore = "requires an enabled physical Windows microphone"]
    fn real_windows_microphone_probe_returns_wasapi_pcm_without_writing_audio() {
        let probe =
            probe_device(AudioFlow::Microphone, None, Duration::from_millis(2_500)).unwrap();
        eprintln!(
            "windows_microphone_probe endpoint={} samples={} rms={} audible={}",
            endpoint_evidence_id(&probe.endpoint_id),
            probe.sample_count,
            probe.rms,
            probe.audible_pcm_seen
        );
        assert!(probe.sample_count >= 16_000);
        assert_eq!(probe.sample_rate_hz, 16_000);
        assert!(!probe.raw_audio_written);
    }

    #[test]
    #[ignore = "requires audible playback on the default Windows render endpoint"]
    fn real_windows_loopback_probe_returns_audible_wasapi_pcm() {
        let probe = probe_device(
            AudioFlow::RenderLoopback,
            None,
            Duration::from_millis(5_000),
        )
        .unwrap();
        eprintln!(
            "windows_loopback_probe endpoint={} samples={} rms={} audible={}",
            endpoint_evidence_id(&probe.endpoint_id),
            probe.sample_count,
            probe.rms,
            probe.audible_pcm_seen
        );
        assert!(probe.sample_count >= 16_000);
        assert!(probe.audible_pcm_seen, "default render endpoint was silent");
        assert_eq!(probe.sample_rate_hz, 16_000);
        assert!(!probe.raw_audio_written);
    }

    #[test]
    #[ignore = "requires an enabled physical Windows microphone and runs a 30-second outage"]
    fn real_windows_microphone_backfills_after_thirty_second_websocket_outage() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let (outage_started_tx, outage_started_rx) = mpsc::sync_channel(1);
        let (server_result_tx, server_result_rx) = mpsc::sync_channel(1);
        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut socket = accept(stream).unwrap();
            let mut initial_frames = 0_u64;
            while initial_frames < 5 {
                if matches!(socket.read().unwrap(), Message::Binary(_)) {
                    initial_frames += 1;
                }
            }
            let _ = socket.close(None);
            drop(socket);
            drop(listener);

            let outage_started = Instant::now();
            outage_started_tx.send(()).unwrap();
            thread::sleep(Duration::from_secs(30));
            let recovered_listener = loop {
                match std::net::TcpListener::bind(address) {
                    Ok(value) => break value,
                    Err(_) => thread::sleep(Duration::from_millis(50)),
                }
            };
            let outage_duration = outage_started.elapsed();
            let (stream, _) = recovered_listener.accept().unwrap();
            let mut socket = accept(stream).unwrap();
            let mut recovered_frames = 0_u64;
            let mut recovered_epoch = 0_u64;
            loop {
                match socket.read().unwrap() {
                    Message::Binary(frame) => {
                        recovered_frames += 1;
                        if recovered_epoch == 0 && frame.len() >= NATIVE_PCM_HEADER_SIZE {
                            recovered_epoch = u64::from_be_bytes(frame[12..20].try_into().unwrap());
                        }
                    }
                    Message::Text(text) if text.as_str() == "END" => {
                        let _ = socket.close(None);
                        break;
                    }
                    Message::Ping(payload) => {
                        socket.send(Message::Pong(payload)).unwrap();
                    }
                    _ => {}
                }
            }
            server_result_tx
                .send((
                    outage_duration,
                    initial_frames,
                    recovered_frames,
                    recovered_epoch,
                ))
                .unwrap();
        });

        let backend = BackendSupervisor::default();
        backend.use_external(format!("http://{address}"));
        let spool_root = std::env::temp_dir().join(format!(
            "meeting-copilot-outage-spool-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let supervisor = WindowsAudioCaptureSupervisor::new(spool_root.clone());
        let session_id = "windows_wasapi_thirty_second_outage".to_string();
        let started = supervisor.start(
            AudioFlow::Microphone,
            Some(session_id.clone()),
            Some(1),
            None,
            &backend,
        );
        assert_eq!(started.status, "recording", "{:?}", started.errors);
        outage_started_rx
            .recv_timeout(Duration::from_secs(10))
            .expect("fault server did not start the outage");

        let deadline = Instant::now() + Duration::from_secs(50);
        let mut recovery_events = Vec::new();
        let recovered = loop {
            let snapshot =
                supervisor.collect_events(AudioFlow::Microphone, Some(session_id.clone()));
            recovery_events.extend(snapshot.events.clone());
            if snapshot.capture_epoch.is_some_and(|epoch| epoch > 1)
                && snapshot.readiness.backfilled_frame_count > 0
                && snapshot.readiness.buffered_frame_count == 0
                && snapshot.readiness.transport_ready
            {
                break snapshot;
            }
            assert!(
                Instant::now() < deadline,
                "30-second outage did not recover"
            );
            thread::sleep(Duration::from_millis(100));
        };
        assert!(recovered.errors.is_empty(), "{:?}", recovered.errors);
        assert!(recovery_events.iter().any(|event| {
            event.get("event_type").and_then(Value::as_str) == Some("capture_recovery")
                && event.get("state").and_then(Value::as_str) == Some("reconnecting")
        }));
        assert!(recovery_events.iter().any(|event| {
            event.get("event_type").and_then(Value::as_str) == Some("capture_recovery")
                && event.get("state").and_then(Value::as_str) == Some("recovered")
        }));

        let stopped = supervisor.stop(AudioFlow::Microphone, Some(&session_id));
        assert_eq!(stopped.status, "stopped", "{:?}", stopped.errors);
        let (outage_duration, initial_frames, recovered_frames, recovered_epoch) = server_result_rx
            .recv_timeout(Duration::from_secs(10))
            .unwrap();
        server.join().unwrap();
        assert!(outage_duration >= Duration::from_secs(30));
        assert_eq!(initial_frames, 5);
        assert!(
            recovered_frames >= 90,
            "only {recovered_frames} frames were backfilled"
        );
        assert!(recovered_epoch > 1);
        assert!(recovered.readiness.backfilled_frame_count >= 90);
        let session_spool_root = spool_root.join("microphone").join(&session_id);
        assert!(
            !session_spool_root.exists()
                || fs::read_dir(&session_spool_root).unwrap().next().is_none()
        );
        let _ = fs::remove_dir_all(spool_root);
    }

    #[test]
    #[ignore = "requires the local backend and an enabled physical Windows microphone"]
    fn real_windows_microphone_streams_native_pcm_v2_to_product_websocket() {
        let base_url = std::env::var("MEETING_COPILOT_WINDOWS_AUDIO_SMOKE_BASE_URL")
            .expect("set MEETING_COPILOT_WINDOWS_AUDIO_SMOKE_BASE_URL to the local backend");
        let session_id = format!(
            "windows_wasapi_{:x}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        );
        let backend = BackendSupervisor::default();
        backend.use_external(base_url);
        let supervisor = WindowsAudioCaptureSupervisor::default();

        let started = supervisor.start(
            AudioFlow::Microphone,
            Some(session_id.clone()),
            Some(1),
            None,
            &backend,
        );
        assert_eq!(started.status, "recording", "{:?}", started.errors);
        assert!(started.readiness.transport_ready);
        assert!(started.readiness.pcm_seen);

        let deadline = Instant::now() + Duration::from_secs(12);
        let running = loop {
            let snapshot = supervisor.status(AudioFlow::Microphone);
            if snapshot.readiness.pcm_seen || !snapshot.errors.is_empty() {
                break snapshot;
            }
            assert!(
                Instant::now() < deadline,
                "WASAPI PCM did not reach the backend"
            );
            thread::sleep(Duration::from_millis(100));
        };
        assert!(running.errors.is_empty(), "{:?}", running.errors);
        assert!(running.readiness.pcm_seen);
        assert!(running.readiness.pcm_bytes_sent >= (NATIVE_FRAME_SAMPLES * 4) as u64);
        thread::sleep(hardware_smoke_capture_duration(2));
        if std::env::var("MEETING_COPILOT_WINDOWS_AUDIO_EXPECT_RECOVERY").as_deref() == Ok("1") {
            let recovery =
                supervisor.collect_events(AudioFlow::Microphone, Some(session_id.clone()));
            assert!(recovery.errors.is_empty(), "{:?}", recovery.errors);
            assert!(
                recovery.capture_epoch.is_some_and(|epoch| epoch > 1),
                "capture epoch did not advance after the injected disconnect"
            );
            assert!(
                recovery.readiness.backfilled_frame_count > 0,
                "the private PCM spool did not backfill any frame"
            );
            assert!(
                recovery.events.iter().any(|event| {
                    event.get("event_type").and_then(Value::as_str) == Some("capture_recovery")
                        && event.get("state").and_then(Value::as_str) == Some("recovered")
                }),
                "the capture recovery lifecycle did not reach recovered"
            );
        }
        let stopped = supervisor.stop(AudioFlow::Microphone, Some(&session_id));
        assert_eq!(stopped.status, "stopped", "{:?}", stopped.errors);
        assert!(!stopped.raw_audio_uploaded);
        assert!(!stopped.writes_raw_audio_files);
        eprintln!(
            "windows_wasapi_product_stream session={} bytes={} rms={:?} audible={}",
            session_id,
            stopped.readiness.pcm_bytes_sent,
            stopped.readiness.first_pcm_rms,
            stopped.readiness.audible_pcm_seen
        );
    }

    #[test]
    #[ignore = "requires local backend plus audible playback on the default render endpoint"]
    fn real_windows_loopback_streams_native_pcm_v2_to_product_websocket() {
        let base_url = std::env::var("MEETING_COPILOT_WINDOWS_AUDIO_SMOKE_BASE_URL")
            .expect("set MEETING_COPILOT_WINDOWS_AUDIO_SMOKE_BASE_URL to the local backend");
        let session_id = format!(
            "windows_loopback_{:x}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        );
        let backend = BackendSupervisor::default();
        backend.use_external(base_url);
        let supervisor = WindowsAudioCaptureSupervisor::default();
        let started = supervisor.start(
            AudioFlow::RenderLoopback,
            Some(session_id.clone()),
            Some(1),
            None,
            &backend,
        );
        assert_eq!(started.status, "recording", "{:?}", started.errors);
        assert!(started.readiness.transport_ready);
        assert!(started.readiness.pcm_seen);

        let deadline = Instant::now() + Duration::from_secs(12);
        let running = loop {
            let snapshot = supervisor.status(AudioFlow::RenderLoopback);
            if snapshot.readiness.audible_pcm_seen || !snapshot.errors.is_empty() {
                break snapshot;
            }
            assert!(
                Instant::now() < deadline,
                "audible loopback PCM did not reach the backend"
            );
            thread::sleep(Duration::from_millis(100));
        };
        assert!(running.errors.is_empty(), "{:?}", running.errors);
        assert!(running.readiness.pcm_seen);
        assert!(running.readiness.audible_pcm_seen);
        thread::sleep(hardware_smoke_capture_duration(6));
        let stopped = supervisor.stop(AudioFlow::RenderLoopback, Some(&session_id));
        assert_eq!(stopped.status, "stopped", "{:?}", stopped.errors);
        assert!(stopped.readiness.pcm_bytes_sent >= (NATIVE_FRAME_SAMPLES * 4) as u64);
        assert!(!stopped.raw_audio_uploaded);
        assert!(!stopped.writes_raw_audio_files);
        eprintln!(
            "windows_loopback_product_stream session={} bytes={} rms={:?} audible={}",
            session_id,
            stopped.readiness.pcm_bytes_sent,
            stopped.readiness.first_pcm_rms,
            stopped.readiness.audible_pcm_seen
        );
    }
}
