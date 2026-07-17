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
    provider_label: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProviderConfigResponse {
    pub command_status: &'static str,
    pub configured: bool,
    pub api_key_present: bool,
    pub base_url: Option<String>,
    pub model: Option<String>,
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
        match self.load() {
            Ok(config) => {
                let errors = self.last_sync_error();
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
            match backend.configure_provider(&config) {
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
        backend: &BackendSupervisor,
    ) -> ProviderConfigResponse {
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
            let metadata = validate_metadata(base_url, model)?;
            let replacing_secret = !api_key.is_empty();
            let resolved_api_key = if replacing_secret {
                validate_api_key(&api_key)?;
                self.credentials.set(&api_key)?;
                api_key
            } else {
                previous
                    .secret
                    .clone()
                    .filter(|_| previous.metadata.is_some())
                    .ok_or_else(|| "请输入 API Key".to_string())?
            };
            persist_metadata(&self.metadata_path, &metadata)?;
            let config = runtime_config(metadata, resolved_api_key);
            backend.configure_provider(&config)?;
            self.set_runtime_synced(true);
            self.set_sync_error(None);
            Ok(config)
        })();
        match result {
            Ok(config) => self.response("ok", Some(&config), Vec::new()),
            Err(error) => {
                let mut errors = vec![error];
                if let Err(rollback_error) = self.restore_snapshot(&previous) {
                    errors.push(rollback_error);
                }
                match previous_config.as_ref() {
                    Some(config) => {
                        if let Err(rollback_error) = backend.configure_provider(config) {
                            errors.push(format!(
                                "failed to restore previous runtime config: {rollback_error}"
                            ));
                        }
                    }
                    None => {
                        let _ = backend.clear_provider();
                    }
                }
                self.set_runtime_synced(previous_config.is_some());
                self.set_sync_error(Some(errors.join("；")));
                self.response("error", previous_config.as_ref(), errors)
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
        if !self.metadata_path.is_file() {
            return Ok(None);
        }
        let raw = fs::read(&self.metadata_path)
            .map_err(|error| format!("failed to read provider metadata: {error}"))?;
        let metadata = decode_metadata(&raw)?;
        let Some(secret) = self.credentials.get()? else {
            return Err(
                "provider metadata exists but the system credential is missing".to_string(),
            );
        };
        Ok(Some(runtime_config(metadata, secret)))
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
        provider_label: metadata.provider_label,
    }
}

fn validate_metadata(base_url: String, model: String) -> Result<ProviderMetadata, String> {
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
    let normalized_model = model.trim();
    if normalized_model.is_empty()
        || normalized_model.len() > 128
        || normalized_model.chars().any(char::is_control)
    {
        return Err("模型名称格式无效".to_string());
    }
    Ok(ProviderMetadata {
        schema_version: METADATA_SCHEMA.to_string(),
        base_url: raw_base_url.trim_end_matches('/').to_string(),
        model: normalized_model.to_string(),
        provider_label: PROVIDER_LABEL.to_string(),
    })
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
    let metadata: ProviderMetadata = serde_json::from_slice(raw)
        .map_err(|error| format!("invalid provider metadata: {error}"))?;
    if metadata.schema_version != METADATA_SCHEMA {
        return Err("unsupported provider metadata schema".to_string());
    }
    Ok(metadata)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

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
        assert!(!raw.contains("api_key"));
        assert!(!raw.contains("sk-"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn failed_backend_sync_restores_previous_metadata_and_secret() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-provider-rollback-{}",
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
        let secret = Arc::new(Mutex::new(Some("sk-old-secret".to_string())));
        let provider = ProviderConfigSupervisor {
            metadata_path: metadata_path.clone(),
            credentials: Box::new(MemoryCredentialStore {
                value: Arc::clone(&secret),
            }),
            operation_lock: Mutex::new(()),
            runtime_synced: Mutex::new(true),
            last_sync_error: Mutex::new(None),
        };

        let response = provider.save(
            "https://new-relay.example".to_string(),
            "sk-new-secret".to_string(),
            "new-model".to_string(),
            &BackendSupervisor::default(),
        );

        assert_eq!(response.command_status, "error");
        assert_eq!(
            response.base_url.as_deref(),
            Some("https://old-relay.example")
        );
        assert_eq!(secret.lock().unwrap().as_deref(), Some("sk-old-secret"));
        let restored = decode_metadata(&fs::read(metadata_path).unwrap()).unwrap();
        assert_eq!(restored.model, "old-model");
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
