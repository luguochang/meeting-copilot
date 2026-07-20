use crate::desktop_backend_supervisor::{BackendSupervisor, RuntimeProviderConfig};
use keyring::{Entry, Error as KeyringError};
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use url::Url;

const METADATA_SCHEMA: &str = "meeting_copilot.provider_config.v1";
const KEYCHAIN_SERVICE: &str = "com.meetingcopilot.desktop.llm";
const KEYCHAIN_ACCOUNT: &str = "gateway-api-key";
const PROVIDER_LABEL: &str = "openai_compatible_gateway";
const API_STYLE_CHAT_COMPLETIONS: &str = "chat_completions";
const API_STYLE_RESPONSES: &str = "responses";

trait CredentialStore: Send + Sync {
    fn get(&self) -> Result<Option<String>, String>;
    fn set(&self, secret: &str) -> Result<(), String>;
    fn delete(&self) -> Result<(), String>;
}

struct SystemCredentialStore;

impl SystemCredentialStore {
    fn entry() -> Result<Entry, String> {
        Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
            .map_err(|error| format!("failed to open system credential store: {error}"))
    }
}

impl CredentialStore for SystemCredentialStore {
    fn get(&self) -> Result<Option<String>, String> {
        match Self::entry()?.get_password() {
            Ok(secret) => Ok(Some(secret)),
            Err(KeyringError::NoEntry) => Ok(None),
            Err(error) => Err(format!("failed to read system credential: {error}")),
        }
    }

    fn set(&self, secret: &str) -> Result<(), String> {
        Self::entry()?
            .set_password(secret)
            .map_err(|error| format!("failed to save system credential: {error}"))
    }

    fn delete(&self) -> Result<(), String> {
        match Self::entry()?.delete_credential() {
            Ok(()) | Err(KeyringError::NoEntry) => Ok(()),
            Err(error) => Err(format!("failed to delete system credential: {error}")),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ProviderMetadata {
    schema_version: String,
    base_url: String,
    model: String,
    #[serde(default)]
    realtime_model: String,
    #[serde(default)]
    api_style: String,
    provider_label: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProviderConfigResponse {
    pub command_status: &'static str,
    pub configured: bool,
    pub api_key_present: bool,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub realtime_model: Option<String>,
    pub api_style: Option<String>,
    pub provider_label: String,
    pub runtime_synced: bool,
    pub errors: Vec<String>,
}

pub struct ProviderConfigSupervisor {
    metadata_path: PathBuf,
    credentials: Box<dyn CredentialStore>,
    operation_lock: Mutex<()>,
    runtime_synced: Mutex<bool>,
    last_sync_error: Mutex<Option<String>>,
}

impl ProviderConfigSupervisor {
    pub fn new(app_data_dir: PathBuf) -> Self {
        Self {
            metadata_path: app_data_dir.join("provider-config.json"),
            credentials: Box::new(SystemCredentialStore),
            operation_lock: Mutex::new(()),
            runtime_synced: Mutex::new(false),
            last_sync_error: Mutex::new(None),
        }
    }

    pub fn status(&self) -> ProviderConfigResponse {
        match self.load_metadata() {
            Ok(config) => {
                let errors = self.last_sync_error();
                // Status is intentionally metadata-only. Reading Keychain here would make every
                // app launch or settings render capable of showing a macOS authorization prompt.
                let config = config.map(|metadata| runtime_config(metadata, String::new()));
                self.response(
                    if errors.is_empty() { "ok" } else { "error" },
                    config.as_ref(),
                    errors,
                )
            }
            Err(error) => self.response("error", None, vec![error]),
        }
    }

    pub fn sync(&self, backend: &BackendSupervisor) -> Result<bool, String> {
        self.sync_with(|config| backend.configure_provider(config))
    }

    fn sync_with(
        &self,
        mut configure_provider: impl FnMut(&RuntimeProviderConfig) -> Result<(), String>,
    ) -> Result<bool, String> {
        let _operation = self.operation_lock.lock().map_err(|_| {
            let error = "AI 配置操作锁异常".to_string();
            self.set_sync_error(Some(error.clone()));
            error
        })?;
        let Some(config) = self.load().map_err(|error| {
            self.set_sync_error(Some(error.clone()));
            error
        })?
        else {
            self.set_runtime_synced(false);
            self.set_sync_error(None);
            return Ok(false);
        };
        let mut last_error = None;
        for attempt in 0..3 {
            match configure_provider(&config) {
                Ok(()) => {
                    self.set_runtime_synced(true);
                    self.set_sync_error(None);
                    return Ok(true);
                }
                Err(error) => {
                    last_error = Some(error);
                    if attempt < 2 {
                        thread::sleep(Duration::from_millis(200));
                    }
                }
            }
        }
        let error = last_error.unwrap_or_else(|| "provider runtime sync failed".to_string());
        self.set_runtime_synced(false);
        self.set_sync_error(Some(error.clone()));
        Err(error)
    }

    pub fn save(
        &self,
        base_url: String,
        api_key: String,
        model: String,
        realtime_model: Option<String>,
        api_style: String,
    ) -> ProviderConfigResponse {
        let _operation = match self.operation_lock.lock() {
            Ok(guard) => guard,
            Err(_) => return self.response("error", None, vec!["AI 配置操作锁异常".to_string()]),
        };
        let previous_metadata = match read_optional_bytes(&self.metadata_path) {
            Ok(metadata) => metadata,
            Err(error) => return self.response("error", None, vec![error]),
        };
        let previous_runtime_synced = self
            .runtime_synced
            .lock()
            .map(|value| *value)
            .unwrap_or(false);
        let result = (|| {
            let metadata =
                validate_metadata_with_models(base_url, model, realtime_model, api_style)?;
            let replacing_secret = !api_key.is_empty();
            if !replacing_secret && previous_metadata.is_none() {
                return Err("请输入 API Key".to_string());
            }
            if replacing_secret {
                validate_api_key(&api_key)?;
            }
            persist_metadata(&self.metadata_path, &metadata)?;
            if replacing_secret {
                if let Err(error) = self.credentials.set(&api_key) {
                    return match restore_optional_bytes(
                        &self.metadata_path,
                        previous_metadata.as_deref(),
                    ) {
                        Ok(()) => Err(error),
                        Err(rollback_error) => {
                            Err(format!("{error}；配置回滚失败: {rollback_error}"))
                        }
                    };
                }
            }
            let config = runtime_config(metadata, String::new());
            // Persisting settings never reads the existing credential or injects it into the
            // backend. Explicit provider sync remains the only operation that may open Keychain.
            self.set_runtime_synced(false);
            self.set_sync_error(None);
            Ok(config)
        })();
        match result {
            Ok(config) => self.response("ok", Some(&config), Vec::new()),
            Err(error) => {
                self.set_runtime_synced(previous_runtime_synced);
                self.set_sync_error(Some(error.clone()));
                let previous_config = previous_metadata
                    .as_deref()
                    .and_then(|raw| decode_metadata(raw).ok())
                    .map(|metadata| runtime_config(metadata, String::new()));
                self.response("error", previous_config.as_ref(), vec![error])
            }
        }
    }

    pub fn clear(&self, backend: &BackendSupervisor) -> ProviderConfigResponse {
        let _operation = match self.operation_lock.lock() {
            Ok(guard) => guard,
            Err(_) => return self.response("error", None, vec!["AI 配置操作锁异常".to_string()]),
        };
        let previous = match self.persistence_snapshot() {
            Ok(snapshot) => snapshot,
            Err(error) => return self.response("error", None, vec![error]),
        };
        let previous_config = previous.runtime_config().ok().flatten();
        let result = (|| {
            backend.clear_provider()?;
            self.credentials.delete()?;
            if self.metadata_path.exists() {
                fs::remove_file(&self.metadata_path)
                    .map_err(|error| format!("failed to remove provider metadata: {error}"))?;
            }
            self.set_runtime_synced(false);
            self.set_sync_error(None);
            Ok(())
        })();
        match result {
            Ok(()) => self.response("ok", None, Vec::new()),
            Err(error) => {
                let mut errors = vec![error];
                if let Err(rollback_error) = self.restore_snapshot(&previous) {
                    errors.push(rollback_error);
                }
                if let Some(config) = previous_config.as_ref() {
                    if let Err(rollback_error) = backend.configure_provider(config) {
                        errors.push(format!(
                            "failed to restore previous runtime config: {rollback_error}"
                        ));
                    }
                }
                self.set_runtime_synced(previous_config.is_some());
                self.set_sync_error(Some(errors.join("；")));
                self.response("error", previous_config.as_ref(), errors)
            }
        }
    }

    fn persistence_snapshot(&self) -> Result<PersistenceSnapshot, String> {
        let metadata = if self.metadata_path.is_file() {
            Some(
                fs::read(&self.metadata_path)
                    .map_err(|error| format!("failed to read provider metadata: {error}"))?,
            )
        } else {
            None
        };
        Ok(PersistenceSnapshot {
            metadata,
            secret: self.credentials.get()?,
        })
    }

    fn restore_snapshot(&self, snapshot: &PersistenceSnapshot) -> Result<(), String> {
        match snapshot.secret.as_deref() {
            Some(secret) => self.credentials.set(secret)?,
            None => self.credentials.delete()?,
        }
        match snapshot.metadata.as_deref() {
            Some(bytes) => persist_bytes(&self.metadata_path, bytes),
            None => {
                if self.metadata_path.exists() {
                    fs::remove_file(&self.metadata_path)
                        .map_err(|error| format!("failed to remove provider metadata: {error}"))?;
                }
                Ok(())
            }
        }
    }

    fn load(&self) -> Result<Option<RuntimeProviderConfig>, String> {
        let Some(metadata) = self.load_metadata()? else {
            return Ok(None);
        };
        let Some(secret) = self.credentials.get()? else {
            return Err(
                "provider metadata exists but the system credential is missing".to_string(),
            );
        };
        Ok(Some(runtime_config(metadata, secret)))
    }

    fn load_metadata(&self) -> Result<Option<ProviderMetadata>, String> {
        if !self.metadata_path.is_file() {
            return Ok(None);
        }
        let raw = fs::read(&self.metadata_path)
            .map_err(|error| format!("failed to read provider metadata: {error}"))?;
        decode_metadata(&raw).map(Some)
    }

    fn response(
        &self,
        command_status: &'static str,
        config: Option<&RuntimeProviderConfig>,
        errors: Vec<String>,
    ) -> ProviderConfigResponse {
        ProviderConfigResponse {
            command_status,
            configured: config.is_some(),
            api_key_present: config.is_some(),
            base_url: config.map(|value| value.base_url.clone()),
            model: config.map(|value| value.model.clone()),
            realtime_model: config.map(|value| value.realtime_model.clone()),
            api_style: config.map(|value| value.api_style.clone()),
            provider_label: config
                .map(|value| value.provider_label.clone())
                .unwrap_or_else(|| PROVIDER_LABEL.to_string()),
            runtime_synced: self
                .runtime_synced
                .lock()
                .map(|value| *value)
                .unwrap_or(false),
            errors,
        }
    }

    fn set_runtime_synced(&self, value: bool) {
        if let Ok(mut current) = self.runtime_synced.lock() {
            *current = value;
        }
    }

    fn set_sync_error(&self, value: Option<String>) {
        if let Ok(mut current) = self.last_sync_error.lock() {
            *current = value;
        }
    }

    fn last_sync_error(&self) -> Vec<String> {
        self.last_sync_error
            .lock()
            .ok()
            .and_then(|value| value.clone())
            .into_iter()
            .collect()
    }
}

struct PersistenceSnapshot {
    metadata: Option<Vec<u8>>,
    secret: Option<String>,
}

impl PersistenceSnapshot {
    fn runtime_config(&self) -> Result<Option<RuntimeProviderConfig>, String> {
        let (Some(raw), Some(secret)) = (self.metadata.as_deref(), self.secret.clone()) else {
            return Ok(None);
        };
        let metadata = decode_metadata(raw)?;
        Ok(Some(runtime_config(metadata, secret)))
    }
}

fn runtime_config(metadata: ProviderMetadata, api_key: String) -> RuntimeProviderConfig {
    RuntimeProviderConfig {
        base_url: metadata.base_url,
        api_key,
        model: metadata.model,
        realtime_model: metadata.realtime_model,
        api_style: metadata.api_style,
        provider_label: metadata.provider_label,
    }
}

#[cfg(test)]
fn validate_metadata(base_url: String, model: String) -> Result<ProviderMetadata, String> {
    validate_metadata_with_models(
        base_url,
        model,
        None,
        API_STYLE_CHAT_COMPLETIONS.to_string(),
    )
}

fn validate_metadata_with_models(
    base_url: String,
    model: String,
    realtime_model: Option<String>,
    api_style: String,
) -> Result<ProviderMetadata, String> {
    let raw_base_url = base_url.trim();
    let parsed =
        Url::parse(raw_base_url).map_err(|_| "中转站地址必须是完整的 http(s) URL".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err("中转站地址必须是完整的 http(s) URL".to_string());
    }
    if parsed.scheme() == "http"
        && !parsed
            .host_str()
            .and_then(|host| host.parse::<IpAddr>().ok())
            .is_some_and(|address| address.is_loopback())
    {
        return Err("远程中转站必须使用 HTTPS".to_string());
    }
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err("中转站地址不能包含账号、查询参数或片段".to_string());
    }
    let normalized_model = validate_model_name(&model)?;
    let normalized_realtime_model = realtime_model
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(validate_model_name)
        .transpose()?
        .unwrap_or_else(|| normalized_model.clone());
    let normalized_api_style = validate_api_style(&api_style)?;
    Ok(ProviderMetadata {
        schema_version: METADATA_SCHEMA.to_string(),
        base_url: raw_base_url.trim_end_matches('/').to_string(),
        model: normalized_model,
        realtime_model: normalized_realtime_model,
        api_style: normalized_api_style,
        provider_label: PROVIDER_LABEL.to_string(),
    })
}

fn validate_model_name(model: &str) -> Result<String, String> {
    let normalized = model.trim();
    if normalized.is_empty() || normalized.len() > 128 || normalized.chars().any(char::is_control) {
        return Err("模型名称格式无效".to_string());
    }
    Ok(normalized.to_string())
}

fn validate_api_style(api_style: &str) -> Result<String, String> {
    let normalized = api_style.trim().to_ascii_lowercase();
    if !matches!(
        normalized.as_str(),
        API_STYLE_CHAT_COMPLETIONS | API_STYLE_RESPONSES
    ) {
        return Err("接口协议必须是 Chat Completions 或 Responses".to_string());
    }
    Ok(normalized)
}

fn validate_api_key(api_key: &str) -> Result<(), String> {
    if api_key.len() < 8 || api_key.len() > 4096 || api_key.chars().any(char::is_control) {
        return Err("API Key 格式无效".to_string());
    }
    Ok(())
}

fn persist_metadata(path: &Path, metadata: &ProviderMetadata) -> Result<(), String> {
    let bytes = serde_json::to_vec_pretty(metadata)
        .map_err(|error| format!("failed to encode provider metadata: {error}"))?;
    persist_bytes(path, &bytes)
}

fn read_optional_bytes(path: &Path) -> Result<Option<Vec<u8>>, String> {
    if !path.is_file() {
        return Ok(None);
    }
    fs::read(path)
        .map(Some)
        .map_err(|error| format!("failed to read provider metadata: {error}"))
}

fn restore_optional_bytes(path: &Path, bytes: Option<&[u8]>) -> Result<(), String> {
    match bytes {
        Some(bytes) => persist_bytes(path, bytes),
        None => {
            if path.exists() {
                fs::remove_file(path)
                    .map_err(|error| format!("failed to remove provider metadata: {error}"))?;
            }
            Ok(())
        }
    }
}

fn persist_bytes(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "provider metadata path has no parent".to_string())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("failed to create provider metadata directory: {error}"))?;
    let temp_path = path.with_extension("json.tmp");
    let mut options = OpenOptions::new();
    options.create(true).write(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temp_path)
        .map_err(|error| format!("failed to open provider metadata: {error}"))?;
    file.write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("failed to persist provider metadata: {error}"))?;
    fs::rename(&temp_path, path)
        .map_err(|error| format!("failed to commit provider metadata: {error}"))
}

fn decode_metadata(raw: &[u8]) -> Result<ProviderMetadata, String> {
    let mut metadata: ProviderMetadata = serde_json::from_slice(raw)
        .map_err(|error| format!("invalid provider metadata: {error}"))?;
    if metadata.schema_version != METADATA_SCHEMA {
        return Err("unsupported provider metadata schema".to_string());
    }
    if metadata.api_style.trim().is_empty() {
        let model = metadata.model.to_ascii_lowercase();
        metadata.api_style = if model.starts_with("gpt-5") || model.contains("codex") {
            API_STYLE_RESPONSES.to_string()
        } else {
            API_STYLE_CHAT_COMPLETIONS.to_string()
        };
    }
    metadata.realtime_model = if metadata.realtime_model.trim().is_empty() {
        metadata.model.clone()
    } else {
        validate_model_name(&metadata.realtime_model)?
    };
    metadata.api_style = validate_api_style(&metadata.api_style)?;
    Ok(metadata)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{mpsc, Arc};

    struct MemoryCredentialStore {
        value: Arc<Mutex<Option<String>>>,
    }

    impl CredentialStore for MemoryCredentialStore {
        fn get(&self) -> Result<Option<String>, String> {
            Ok(self.value.lock().unwrap().clone())
        }

        fn set(&self, secret: &str) -> Result<(), String> {
            *self.value.lock().unwrap() = Some(secret.to_string());
            Ok(())
        }

        fn delete(&self) -> Result<(), String> {
            *self.value.lock().unwrap() = None;
            Ok(())
        }
    }

    #[test]
    fn metadata_validation_normalizes_url_without_accepting_embedded_credentials() {
        let metadata = validate_metadata(
            "https://relay.example/root/".to_string(),
            "gpt-test".to_string(),
        )
        .unwrap();
        assert_eq!(metadata.base_url, "https://relay.example/root");
        assert_eq!(metadata.model, "gpt-test");
        assert_eq!(metadata.realtime_model, "gpt-test");
        assert_eq!(metadata.api_style, API_STYLE_CHAT_COMPLETIONS);
        assert!(validate_metadata(
            "https://user:pass@relay.example".to_string(),
            "gpt-test".to_string(),
        )
        .is_err());
        assert!(
            validate_metadata("http://relay.example".to_string(), "gpt-test".to_string(),).is_err()
        );
        assert!(
            validate_metadata("http://127.0.0.1:8000".to_string(), "gpt-test".to_string(),).is_ok()
        );
    }

    #[test]
    fn legacy_gpt5_metadata_migrates_to_explicit_responses_style() {
        let raw = br#"{
            "schema_version":"meeting_copilot.provider_config.v1",
            "base_url":"https://relay.example",
            "model":"gpt-5.5",
            "provider_label":"openai_compatible_gateway"
        }"#;

        let metadata = decode_metadata(raw).unwrap();

        assert_eq!(metadata.api_style, API_STYLE_RESPONSES);
        assert_eq!(metadata.realtime_model, "gpt-5.5");
    }

    #[test]
    fn persisted_metadata_never_contains_api_key() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-provider-metadata-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let metadata =
            validate_metadata("https://relay.example".to_string(), "gpt-test".to_string()).unwrap();
        let path = root.join("provider-config.json");
        persist_metadata(&path, &metadata).unwrap();
        let raw = fs::read_to_string(path).unwrap();
        assert!(raw.contains("\"realtime_model\": \"gpt-test\""));
        assert!(!raw.contains("api_key"));
        assert!(!raw.contains("sk-"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn status_reads_metadata_without_accessing_the_credential_store() {
        struct RejectCredentialReads;

        impl CredentialStore for RejectCredentialReads {
            fn get(&self) -> Result<Option<String>, String> {
                Err("credential store must not be read for status".to_string())
            }

            fn set(&self, _secret: &str) -> Result<(), String> {
                Ok(())
            }

            fn delete(&self) -> Result<(), String> {
                Ok(())
            }
        }

        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-provider-status-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let metadata_path = root.join("provider-config.json");
        let metadata =
            validate_metadata("https://relay.example".to_string(), "gpt-test".to_string()).unwrap();
        persist_metadata(&metadata_path, &metadata).unwrap();
        let provider = ProviderConfigSupervisor {
            metadata_path,
            credentials: Box::new(RejectCredentialReads),
            operation_lock: Mutex::new(()),
            runtime_synced: Mutex::new(false),
            last_sync_error: Mutex::new(None),
        };

        let response = provider.status();

        assert_eq!(response.command_status, "ok");
        assert!(response.configured);
        assert!(response.api_key_present);
        assert!(!response.runtime_synced);
        assert_eq!(response.base_url.as_deref(), Some("https://relay.example"));
        assert_eq!(response.model.as_deref(), Some("gpt-test"));
        assert_eq!(response.realtime_model.as_deref(), Some("gpt-test"));
        assert_eq!(
            response.api_style.as_deref(),
            Some(API_STYLE_CHAT_COMPLETIONS)
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn sync_holds_the_operation_lock_through_runtime_injection() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-provider-sync-lock-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let metadata_path = root.join("provider-config.json");
        let metadata =
            validate_metadata("https://relay.example".to_string(), "gpt-test".to_string()).unwrap();
        persist_metadata(&metadata_path, &metadata).unwrap();
        let provider = Arc::new(ProviderConfigSupervisor {
            metadata_path,
            credentials: Box::new(MemoryCredentialStore {
                value: Arc::new(Mutex::new(Some("sk-old-secret".to_string()))),
            }),
            operation_lock: Mutex::new(()),
            runtime_synced: Mutex::new(false),
            last_sync_error: Mutex::new(None),
        });
        let (injection_started_tx, injection_started_rx) = mpsc::channel();
        let (release_injection_tx, release_injection_rx) = mpsc::channel();
        let sync_provider = Arc::clone(&provider);

        let sync = thread::spawn(move || {
            sync_provider.sync_with(|config| {
                assert_eq!(config.api_key, "sk-old-secret");
                assert_eq!(config.model, "gpt-test");
                assert_eq!(config.realtime_model, "gpt-test");
                injection_started_tx.send(()).unwrap();
                release_injection_rx
                    .recv_timeout(Duration::from_secs(2))
                    .unwrap();
                Ok(())
            })
        });

        injection_started_rx
            .recv_timeout(Duration::from_secs(2))
            .unwrap();
        assert!(provider.operation_lock.try_lock().is_err());
        release_injection_tx.send(()).unwrap();
        assert_eq!(sync.join().unwrap(), Ok(true));
        assert!(provider.operation_lock.try_lock().is_ok());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn saving_existing_metadata_with_blank_key_never_reads_credentials() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-provider-save-metadata-only-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let metadata_path = root.join("provider-config.json");
        let previous_metadata = validate_metadata(
            "https://old-relay.example".to_string(),
            "old-model".to_string(),
        )
        .unwrap();
        persist_metadata(&metadata_path, &previous_metadata).unwrap();

        struct RejectCredentialReads;

        impl CredentialStore for RejectCredentialReads {
            fn get(&self) -> Result<Option<String>, String> {
                Err("save must not read the existing credential".to_string())
            }

            fn set(&self, _secret: &str) -> Result<(), String> {
                Err("blank-key metadata save must not replace the credential".to_string())
            }

            fn delete(&self) -> Result<(), String> {
                Ok(())
            }
        }

        let provider = ProviderConfigSupervisor {
            metadata_path: metadata_path.clone(),
            credentials: Box::new(RejectCredentialReads),
            operation_lock: Mutex::new(()),
            runtime_synced: Mutex::new(true),
            last_sync_error: Mutex::new(None),
        };

        let response = provider.save(
            "https://new-relay.example".to_string(),
            String::new(),
            "new-model".to_string(),
            Some("new-realtime-model".to_string()),
            API_STYLE_RESPONSES.to_string(),
        );

        assert_eq!(response.command_status, "ok");
        assert_eq!(
            response.base_url.as_deref(),
            Some("https://new-relay.example")
        );
        assert!(!response.runtime_synced);
        assert_eq!(response.api_style.as_deref(), Some(API_STYLE_RESPONSES));
        let saved = decode_metadata(&fs::read(metadata_path).unwrap()).unwrap();
        assert_eq!(saved.model, "new-model");
        assert_eq!(saved.realtime_model, "new-realtime-model");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn invalid_replacement_key_does_not_change_existing_metadata() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-provider-invalid-key-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let metadata_path = root.join("provider-config.json");
        let previous_metadata = validate_metadata(
            "https://old-relay.example".to_string(),
            "old-model".to_string(),
        )
        .unwrap();
        persist_metadata(&metadata_path, &previous_metadata).unwrap();
        let provider = ProviderConfigSupervisor {
            metadata_path: metadata_path.clone(),
            credentials: Box::new(MemoryCredentialStore {
                value: Arc::new(Mutex::new(Some("sk-old-secret".to_string()))),
            }),
            operation_lock: Mutex::new(()),
            runtime_synced: Mutex::new(false),
            last_sync_error: Mutex::new(None),
        };

        let response = provider.save(
            "https://new-relay.example".to_string(),
            "bad".to_string(),
            "new-model".to_string(),
            None,
            API_STYLE_CHAT_COMPLETIONS.to_string(),
        );

        assert_eq!(response.command_status, "error");
        let unchanged = decode_metadata(&fs::read(metadata_path).unwrap()).unwrap();
        assert_eq!(unchanged.base_url, "https://old-relay.example");
        assert_eq!(unchanged.model, "old-model");
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(target_os = "macos")]
    #[test]
    #[ignore = "explicit local macOS Keychain integration smoke"]
    fn system_keychain_round_trip_survives_a_new_entry_instance() {
        let service = format!("com.meetingcopilot.desktop.test.{}", std::process::id());
        let account = "temporary-round-trip";
        let secret = "sk-temporary-keychain-smoke";
        let first = Entry::new(&service, account).unwrap();
        let _ = first.delete_credential();
        first.set_password(secret).unwrap();
        let second = Entry::new(&service, account).unwrap();
        let observed = second.get_password();
        let cleanup = second.delete_credential();
        assert_eq!(observed.unwrap(), secret);
        cleanup.unwrap();
    }
}
