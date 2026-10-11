#![no_std]
//! Filesystem-only guards; no NOR, card geometry, display or application version pins.

use core::ffi::CStr;
use rustix::{
    fd::{AsFd, OwnedFd},
    fs::{self, AtFlags, FileType, Mode, OFlags, CWD},
    io::{self, Errno},
};

// These three paths belong to the stock launcher profile, not to the FD primitives.
pub const CARD_ROOT: &CStr = c"/mnt/sdcard";
pub const HELPER: &CStr = c"system/itrampoline";
pub const TOKEN: &CStr = c"game/iboxed trampoline.itr";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Guard(&'static str),
    Io(Errno),
}
pub type Result<T> = core::result::Result<T, Error>;
impl From<Errno> for Error {
    fn from(error: Errno) -> Self {
        Self::Io(error)
    }
}

pub fn validate_relative(path: &[u8]) -> Result<()> {
    if path.is_empty() || path.len() >= 1024 {
        return Err(Error::Guard("Relative path length"));
    }
    for part in path.split(|byte| *byte == b'/') {
        if part.is_empty() || part == b"." || part == b".."
            || part.iter().any(|byte| matches!(*byte, 0 | b'\n' | b'\r'))
        {
            return Err(Error::Guard("Unsafe relative path"));
        }
    }
    Ok(())
}

fn open_directory(parent: impl AsFd, name: &CStr) -> Result<OwnedFd> {
    Ok(fs::openat(parent, name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty())?)
}

pub struct Volume {
    pub root: OwnedFd,
    device: u64,
}

impl Volume {
    pub fn open(path: &CStr) -> Result<Self> {
        let bytes = path.to_bytes().strip_prefix(b"/").ok_or(Error::Guard("Absolute root required"))?;
        validate_relative(bytes)?;
        let mut directory = open_directory(CWD, c"/")?;
        let filesystem_root = fs::fstat(&directory)?;
        let mut buffer = [0u8; 1024];
        for component in bytes.split(|byte| *byte == b'/') {
            buffer[..component.len()].copy_from_slice(component);
            buffer[component.len()] = 0;
            let name = CStr::from_bytes_with_nul(&buffer[..component.len() + 1])
                .map_err(|_| Error::Guard("Root component"))?;
            directory = open_directory(&directory, name)?;
        }
        let metadata = fs::fstat(&directory)?;
        if metadata.st_dev == filesystem_root.st_dev && metadata.st_ino == filesystem_root.st_ino {
            return Err(Error::Guard("Root filesystem refused"));
        }
        Ok(Self { root: directory, device: metadata.st_dev })
    }

    pub fn validate_path(&self, path: &CStr) -> Result<()> {
        let current = Self::open(path)?;
        let opened = fs::fstat(&self.root)?;
        let now = fs::fstat(&current.root)?;
        if opened.st_dev != now.st_dev || opened.st_ino != now.st_ino {
            return Err(Error::Guard("Volume root replaced"));
        }
        Ok(())
    }

    pub fn open_file(&self, path: &CStr, executable: bool) -> Result<OwnedFd> {
        let bytes = path.to_bytes();
        validate_relative(bytes)?;
        let mut components = bytes.split(|byte| *byte == b'/').peekable();
        let mut parent = rustix::io::dup(&self.root)?;
        let mut buffer = [0u8; 1024];
        while let Some(component) = components.next() {
            buffer[..component.len()].copy_from_slice(component);
            buffer[component.len()] = 0;
            let name = CStr::from_bytes_with_nul(&buffer[..component.len() + 1])
                .map_err(|_| Error::Guard("File component"))?;
            if components.peek().is_some() {
                parent = open_directory(&parent, name)?;
                if fs::fstat(&parent)?.st_dev != self.device {
                    return Err(Error::Guard("Nested filesystem"));
                }
            } else {
                let descriptor = fs::openat(&parent, name,
                    OFlags::RDONLY | OFlags::NONBLOCK | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                    Mode::empty())?;
                let metadata = fs::fstat(&descriptor)?;
                if FileType::from_raw_mode(metadata.st_mode) != FileType::RegularFile
                    || metadata.st_nlink != 1 || metadata.st_dev != self.device
                    || (executable && metadata.st_mode & 0o111 == 0)
                {
                    return Err(Error::Guard("File type, link, device or execute permission"));
                }
                return Ok(descriptor);
            }
        }
        Err(Error::Guard("Empty file path"))
    }

    pub fn read_target<'buffer>(&self, buffer: &'buffer mut [u8; 1024]) -> Result<&'buffer CStr> {
        let descriptor = self.open_file(TOKEN, false)?;
        let mut used = 0;
        loop {
            if used == buffer.len() {
                let mut extra = [0u8; 1];
                if io::read(&descriptor, &mut extra)? != 0 {
                    return Err(Error::Guard("Token too large"));
                }
                break;
            }
            let count = io::read(&descriptor, &mut buffer[used..])?;
            if count == 0 {
                break;
            }
            used += count;
        }
        if used < 2 || buffer[used - 1] != b'\n' {
            return Err(Error::Guard("Token newline"));
        }
        validate_relative(&buffer[..used - 1])?;
        buffer[used - 1] = 0;
        CStr::from_bytes_with_nul(&buffer[..used]).map_err(|_| Error::Guard("Token NUL"))
    }
}

/// No descriptor cleanup, signals or cwd changes while the frontend is alive.
pub fn exec(descriptor: impl AsFd, name: &CStr) -> Error {
    let arguments = [name.as_ptr().cast::<u8>(), core::ptr::null()];
    let environment = [core::ptr::null()];
    let error = unsafe {
        rustix::runtime::execveat(descriptor, c"", arguments.as_ptr(), environment.as_ptr(), AtFlags::EMPTY_PATH)
    };
    Error::Io(error)
}
