//! A self-executing host fixture; never calls the production SD-root launcher.
use std::{ffi::CString, fs, os::unix::{ffi::OsStrExt, process::CommandExt}, path::PathBuf, process::{Command, ExitCode, Stdio}};
use itrampoline_runtime::Volume;

struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        if let Err(error) = fs::remove_dir_all(&self.0) {
            if std::thread::panicking() {
                eprintln!("Process fixture cleanup failed: {error}");
            } else {
                panic!("Process fixture cleanup failed: {error}");
            }
        }
    }
}

fn check_sanitized(after_rust_start: bool) {
    assert_eq!(unsafe { libc::fcntl(1000, libc::F_GETFD) }, -1);
    for descriptor in 0..3 {
        assert_eq!(fs::read_link(format!("/proc/self/fd/{descriptor}")).unwrap(), PathBuf::from("/dev/null"));
    }
    let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
    for signal in [libc::SIGPIPE, libc::SIGCHLD] {
        assert_eq!(unsafe { libc::sigaction(signal, std::ptr::null(), &mut action) }, 0);
        // A new Rust std executable deliberately ignores SIGPIPE during startup.
        let expected = if after_rust_start && signal == libc::SIGPIPE { libc::SIG_IGN } else { libc::SIG_DFL };
        assert_eq!(action.sa_sigaction, expected);
    }
    let mut mask: libc::sigset_t = unsafe { std::mem::zeroed() };
    assert_eq!(unsafe { libc::sigprocmask(libc::SIG_SETMASK, std::ptr::null(), &mut mask) }, 0);
    assert_eq!(unsafe { libc::sigismember(&mask, libc::SIGUSR1) }, 0);
    let mut timer: libc::itimerval = unsafe { std::mem::zeroed() };
    for kind in [libc::ITIMER_REAL, libc::ITIMER_VIRTUAL, libc::ITIMER_PROF] {
        assert_eq!(unsafe { libc::getitimer(kind, &mut timer) }, 0);
        assert_eq!(timer.it_value.tv_sec, 0);
        assert_eq!(timer.it_value.tv_usec, 0);
    }
}

fn main() -> ExitCode {
    if std::env::args_os().next().as_deref() == Some(std::ffi::OsStr::new("itrampoline-clean-child")) {
        assert!(std::env::vars_os().next().is_none());
        check_sanitized(true);
        return ExitCode::SUCCESS;
    }
    if let Some(directory) = std::env::var_os("ITRAMPOLINE_TEST_CHILD") {
        unsafe { itrampoline_helper::sanitize_inherited() }.unwrap();
        check_sanitized(false);
        let directory = PathBuf::from(directory);
        let path = CString::new(directory.as_os_str().as_bytes()).unwrap();
        let volume = Volume::open(&path).unwrap();
        let app = volume.open_file(c"fixture", true).unwrap();
        fs::rename(directory.join("fixture"), directory.join("held-elf")).unwrap();
        fs::write(directory.join("fixture"), b"not an executable").unwrap();
        let error = itrampoline_runtime::exec(&app, c"itrampoline-clean-child");
        panic!("Pinned native exec failed: {error:?}");
    }

    let directory = std::env::temp_dir().join(format!("itrampoline-process-{}", std::process::id()));
    fs::create_dir(&directory).unwrap();
    let scratch = Scratch(directory);
    fs::copy(std::env::current_exe().unwrap(), scratch.0.join("fixture")).unwrap();
    let mut command = Command::new(std::env::current_exe().unwrap());
    command.env("ITRAMPOLINE_TEST_CHILD", &scratch.0).env("UNEXPECTED_ENV", "must not survive")
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    unsafe {
        command.pre_exec(|| {
            if libc::dup2(0, 1000) != 1000 {
                return Err(std::io::Error::last_os_error());
            }
            libc::signal(libc::SIGPIPE, libc::SIG_IGN);
            libc::signal(libc::SIGCHLD, libc::SIG_IGN);
            let mut mask: libc::sigset_t = std::mem::zeroed();
            libc::sigemptyset(&mut mask);
            libc::sigaddset(&mut mask, libc::SIGUSR1);
            libc::sigprocmask(libc::SIG_SETMASK, &mask, std::ptr::null_mut());
            let timer = libc::itimerval { it_interval: libc::timeval { tv_sec: 60, tv_usec: 0 },
                it_value: libc::timeval { tv_sec: 60, tv_usec: 0 } };
            for kind in [libc::ITIMER_REAL, libc::ITIMER_VIRTUAL, libc::ITIMER_PROF] {
                if libc::setitimer(kind, &timer, std::ptr::null_mut()) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
            }
            Ok(())
        });
    }
    assert!(command.status().unwrap().success(), "Isolated sanitize/FD-exec fixture failed");
    println!("sanitize/FD-exec fixture: passed");
    ExitCode::SUCCESS
}
