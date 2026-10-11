use itrampoline_runtime::{validate_relative, Error, Volume};
use rustix::io::Errno;
use std::{
    ffi::CString,
    fs,
    os::unix::{ffi::OsStrExt, fs::PermissionsExt},
    path::PathBuf,
    sync::atomic::{AtomicUsize, Ordering},
};

static NEXT: AtomicUsize = AtomicUsize::new(0);

struct Fixture {
    directory: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let directory = std::env::temp_dir().join(format!(
            "itrampoline-files-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory).unwrap();
        let fixture = Self { directory };
        fs::create_dir(fixture.root()).unwrap();
        fs::create_dir(fixture.root().join("game")).unwrap();
        fs::create_dir(fixture.root().join("system")).unwrap();
        fixture
    }

    fn root(&self) -> PathBuf {
        self.directory.join("sd")
    }

    fn path(&self) -> CString {
        CString::new(self.root().as_os_str().as_bytes()).unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        if let Err(error) = fs::remove_dir_all(&self.directory) {
            if std::thread::panicking() {
                eprintln!("Fixture cleanup failed: {error}");
            } else {
                panic!("Fixture cleanup failed: {error}");
            }
        }
    }
}

#[test]
fn relative_paths_refuse_escapes_nuls_and_multiline_tokens() {
    assert_eq!(validate_relative(b""), Err(Error::Guard("Relative path length")));

    for bad in [b"/app".as_slice(), b"../app", b"system/./app", b"app//name", b"app/", b"app\0x", b"app\nx"] {
        assert_eq!(validate_relative(bad), Err(Error::Guard("Unsafe relative path")), "{bad:?}");
    }

    assert!(validate_relative(b"system/iman/iman").is_ok());
}

#[test]
fn held_file_survives_name_replacement_and_guards_refuse_links() {
    let fixture = Fixture::new();
    let path = fixture.root().join("system/app");
    fs::write(&path, b"original").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    let volume = Volume::open(&fixture.path()).unwrap();
    let descriptor = volume.open_file(c"system/app", true).unwrap();
    fs::rename(&path, fixture.root().join("system/old")).unwrap();
    fs::write(&path, b"replacement").unwrap();
    let mut buffer = [0u8; 8];

    assert_eq!(rustix::io::read(&descriptor, &mut buffer).unwrap(), 8);
    assert_eq!(&buffer, b"original");
    assert_eq!(volume.open_file(c"system/app", true).unwrap_err(),
        Error::Guard("File type, link, device or execute permission"));

    fs::hard_link(&path, fixture.root().join("system/link")).unwrap();

    assert_eq!(volume.open_file(c"system/app", false).unwrap_err(),
        Error::Guard("File type, link, device or execute permission"));

    std::os::unix::fs::symlink("old", fixture.root().join("system/symlink")).unwrap();

    assert_eq!(volume.open_file(c"system/symlink", true).unwrap_err(), Error::Io(Errno::LOOP));
}

#[test]
fn root_replacement_and_intermediate_symlink_are_refused() {
    let fixture = Fixture::new();
    let volume = Volume::open(&fixture.path()).unwrap();
    fs::rename(fixture.root().join("system"), fixture.root().join("old-system")).unwrap();
    std::os::unix::fs::symlink("old-system", fixture.root().join("system")).unwrap();

    assert_eq!(volume.open_file(c"system/app", false).unwrap_err(), Error::Io(Errno::NOTDIR));

    // Both names stay inside the owned fixture, including an early test failure.
    fs::rename(fixture.root(), fixture.directory.join("old-root")).unwrap();
    fs::create_dir(fixture.root()).unwrap();

    assert_eq!(volume.validate_path(&fixture.path()), Err(Error::Guard("Volume root replaced")));
}

#[test]
fn token_selects_only_a_single_safe_relative_executable() {
    let fixture = Fixture::new();
    let volume = Volume::open(&fixture.path()).unwrap();
    let mut buffer = [0u8; 1024];

    for (token, message) in [
        (b"../app\n".as_slice(), "Unsafe relative path"),
        (b"app", "Token newline"),
        (b"/app\n", "Unsafe relative path"),
        (b"app\nextra\n", "Unsafe relative path"),
    ] {
        fs::write(fixture.root().join("game/iboxed trampoline.itr"), token).unwrap();

        assert_eq!(volume.read_target(&mut buffer), Err(Error::Guard(message)));
    }

    fs::write(fixture.root().join("game/iboxed trampoline.itr"), b"system/iman/iman\n").unwrap();

    assert_eq!(volume.read_target(&mut buffer).unwrap(), c"system/iman/iman");
}
