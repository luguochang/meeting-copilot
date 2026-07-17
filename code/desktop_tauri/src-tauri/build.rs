#[path = "src/app_command_manifest.rs"]
mod app_command_manifest;

fn main() {
    let attributes = tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(app_command_manifest::APP_COMMAND_NAMES),
    );
    tauri_build::try_build(attributes).expect("failed to build Meeting Copilot Tauri metadata");
}
