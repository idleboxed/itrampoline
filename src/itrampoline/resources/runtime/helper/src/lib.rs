//! Operations permitted only after the frontend was replaced by exec.

use itrampoline_runtime::{Error, Result, Volume, CARD_ROOT};
use rustix::{fd::AsRawFd, fs::{self, FileType, Mode, OFlags, CWD}};

fn read_last_error() -> Error {
    rustix::io::Errno::from_raw_os_error(
        std::io::Error::last_os_error().raw_os_error().unwrap_or(libc::EIO)
    ).into()
}

/// # Safety
/// Call once, immediately after exec, single-threaded, before owning any persistent
/// descriptor. Closing raw frontend descriptors in a live frontend is forbidden.
pub unsafe fn sanitize_inherited() -> Result<()> {
    let mut task_count = 0;
    for task in std::fs::read_dir("/proc/self/task").map_err(|_| Error::Guard("Task enumeration"))? {
        task.map_err(|_| Error::Guard("Task enumeration"))?;
        task_count += 1;
    }
    if task_count != 1 {
        return Err(Error::Guard("Single thread required"));
    }
    // Finish enumeration before closing; read_dir's own FD has already been dropped.
    let mut descriptors = Vec::new();
    for entry in std::fs::read_dir("/proc/self/fd").map_err(|_| Error::Guard("FD enumeration"))? {
        let entry = entry.map_err(|_| Error::Guard("FD enumeration"))?;
        let name = entry.file_name();
        let descriptor = name.to_str().and_then(|value| value.parse::<i32>().ok())
            .ok_or(Error::Guard("FD name"))?;
        if descriptor >= 3 {
            descriptors.push(descriptor);
        }
    }
    for descriptor in descriptors {
        // The enumeration FD is closed already. Do not retry close on EINTR.
        if libc::close(descriptor) != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::EBADF) {
            return Err(read_last_error());
        }
    }
    let null = fs::openat(CWD, c"/dev/null", OFlags::RDWR | OFlags::NOFOLLOW | OFlags::CLOEXEC, Mode::empty())?;
    let metadata = fs::fstat(&null)?;
    if FileType::from_raw_mode(metadata.st_mode) != FileType::CharacterDevice
        || fs::major(metadata.st_rdev) != 1 || fs::minor(metadata.st_rdev) != 3
    {
        return Err(Error::Guard("/dev/null identity"));
    }
    let source = rustix::io::fcntl_dupfd_cloexec(&null, 3)?;
    drop(null);
    for descriptor in 0..3 {
        if libc::dup2(source.as_raw_fd(), descriptor) != descriptor {
            return Err(read_last_error());
        }
    }
    let timer: libc::itimerval = std::mem::zeroed();
    for kind in [libc::ITIMER_REAL, libc::ITIMER_VIRTUAL, libc::ITIMER_PROF] {
        if libc::setitimer(kind, &timer, std::ptr::null_mut()) != 0 {
            return Err(read_last_error());
        }
    }
    for signal in 1..=64 {
        // glibc reserves 32/33; no callers can configure their disposition normally.
        if [libc::SIGKILL, libc::SIGSTOP, 32, 33].contains(&signal) {
            continue;
        }
        if libc::signal(signal, libc::SIG_DFL) == libc::SIG_ERR {
            return Err(read_last_error());
        }
    }
    let mut mask: libc::sigset_t = std::mem::zeroed();
    if libc::sigemptyset(&mut mask) != 0
        || libc::sigprocmask(libc::SIG_SETMASK, &mask, std::ptr::null_mut()) != 0
    {
        return Err(read_last_error());
    }
    Ok(())
}

pub fn launch() -> Result<()> {
    let volume = Volume::open(CARD_ROOT)?;
    let mut buffer = [0u8; 1024];
    let target = volume.read_target(&mut buffer)?;
    let executable = volume.open_file(target, true)?;
    // Require an ELF, not a shell script whose interpreter would need a leaked FD.
    let mut header = [0u8; 4];
    if rustix::io::read(&executable, &mut header)? != 4 || header != *b"\x7fELF" {
        return Err(Error::Guard("Native ELF required"));
    }
    volume.validate_path(CARD_ROOT)?;
    // Do not retain the SD as the cwd; exec supplies no environment or arguments.
    rustix::process::chdir(c"/")?;
    Err(itrampoline_runtime::exec(&executable, target))
}
