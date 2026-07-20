use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::Path;

#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

pub fn ensure_private_directory(path: &Path) -> io::Result<()> {
    if path
        .symlink_metadata()
        .is_ok_and(|metadata| metadata.file_type().is_symlink())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "private storage directory must not be a symlink",
        ));
    }
    fs::create_dir_all(path)?;
    if !path.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "private storage path must be a directory",
        ));
    }
    #[cfg(unix)]
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

pub fn open_private_file(path: &Path, append: bool) -> io::Result<File> {
    if path
        .symlink_metadata()
        .is_ok_and(|metadata| metadata.file_type().is_symlink())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "private storage file must not be a symlink",
        ));
    }
    if let Some(parent) = path.parent() {
        ensure_private_directory(parent)?;
    }
    let mut options = OpenOptions::new();
    options
        .create(true)
        .write(true)
        .append(append)
        .truncate(!append);
    #[cfg(unix)]
    options.mode(0o600);
    let file = options.open(path)?;
    harden_private_file(path)?;
    Ok(file)
}

pub fn harden_private_file(path: &Path) -> io::Result<()> {
    let metadata = path.symlink_metadata()?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "private storage path must be a regular file",
        ));
    }
    #[cfg(unix)]
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[cfg(unix)]
    #[test]
    fn directories_and_files_are_owner_only_and_symlinks_fail_closed() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-private-storage-{}-{nonce}",
            std::process::id()
        ));
        let directory = root.join("private");
        ensure_private_directory(&directory).unwrap();
        let file_path = directory.join("runtime.log");
        drop(open_private_file(&file_path, false).unwrap());

        assert_eq!(
            fs::metadata(&directory).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(&file_path).unwrap().permissions().mode() & 0o777,
            0o600
        );

        use std::os::unix::fs::symlink;
        let linked = root.join("linked.log");
        symlink(&file_path, &linked).unwrap();
        assert!(open_private_file(&linked, false).is_err());
        let _ = fs::remove_dir_all(root);
    }
}
