#![no_std]
//! libretro API v1. No frontend FD cleanup or application-specific dependencies.

#[cfg(not(target_env = "musl"))]
extern crate std;

use core::{cell::UnsafeCell, ffi::{c_char, c_void, CStr}, sync::atomic::{AtomicBool, Ordering}};
use itrampoline_runtime::{Result, Volume, CARD_ROOT, HELPER};

#[cfg(all(target_env = "musl", not(test)))]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo<'_>) -> ! {
    rustix::runtime::exit_group(71)
}

// EHABI must never unwind into a vendor frontend even with panic=abort.
#[cfg(target_arch = "arm")]
core::arch::global_asm!(
    ".text", ".arm", ".p2align 2",
    ".global rust_eh_personality, __aeabi_unwind_cpp_pr0, __aeabi_unwind_cpp_pr1",
    ".hidden rust_eh_personality, __aeabi_unwind_cpp_pr0, __aeabi_unwind_cpp_pr1",
    ".type rust_eh_personality, %function",
    ".type __aeabi_unwind_cpp_pr0, %function",
    ".type __aeabi_unwind_cpp_pr1, %function",
    "rust_eh_personality:", "__aeabi_unwind_cpp_pr0:", "__aeabi_unwind_cpp_pr1:",
    "b {abort}", abort = sym abort_unwind,
);
#[cfg(target_arch = "arm")]
extern "C" fn abort_unwind() -> ! {
    rustix::runtime::exit_group(71)
}

type Environment = unsafe extern "C" fn(u32, *mut c_void) -> bool;
type Video = unsafe extern "C" fn(*const c_void, u32, u32, usize);
type Audio = unsafe extern "C" fn(i16, i16);
type AudioBatch = unsafe extern "C" fn(*const i16, usize) -> usize;
type Poll = unsafe extern "C" fn();
type Input = unsafe extern "C" fn(u32, u32, u32, u32) -> i16;

struct State {
    environment: Option<Environment>,
    loaded: bool,
    attempted: bool,
}
struct Shared(UnsafeCell<State>);
// Access is serialized by BUSY, including reentrant frontend callbacks.
unsafe impl Sync for Shared {}
static BUSY: AtomicBool = AtomicBool::new(false);
static STATE: Shared = Shared(UnsafeCell::new(State { environment: None, loaded: false, attempted: false }));

fn apply_to_state<Value>(action: impl FnOnce(&mut State) -> Value) -> Option<Value> {
    if BUSY.compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed).is_err() {
        return None;
    }
    let result = action(unsafe { &mut *STATE.0.get() });
    BUSY.store(false, Ordering::Release);
    Some(result)
}

fn launch_helper() -> Result<()> {
    let volume = Volume::open(CARD_ROOT)?;
    let mut buffer = [0u8; 1024];
    let target = volume.read_target(&mut buffer)?;
    // Missing/unsafe application fails while the frontend can still return to GUI.
    let _application = volume.open_file(target, true)?;
    let helper = volume.open_file(HELPER, true)?;
    volume.validate_path(CARD_ROOT)?;
    Err(itrampoline_runtime::exec(&helper, c"itrampoline"))
}

#[repr(C)]
pub struct GameInfo {
    pub path: *const c_char,
    pub data: *const c_void,
    pub size: usize,
    pub meta: *const c_char,
}
#[repr(C)]
pub struct SystemInfo {
    pub name: *const c_char,
    pub version: *const c_char,
    pub extensions: *const c_char,
    pub fullpath: bool,
    pub block_extract: bool,
}
#[repr(C)]
pub struct Geometry {
    pub base_width: u32,
    pub base_height: u32,
    pub max_width: u32,
    pub max_height: u32,
    pub aspect: f32,
}
#[repr(C)]
pub struct Timing { pub fps: f64, pub sample_rate: f64 }
#[repr(C)]
pub struct AvInfo { pub geometry: Geometry, pub timing: Timing }
#[repr(C)]
struct Message { text: *const c_char, frames: u32 }

/// # Safety
/// `info` and its NUL-terminated path must satisfy the libretro API contract.
unsafe fn has_token_path(info: *const GameInfo) -> bool {
    if info.is_null() || (*info).path.is_null() {
        return false;
    }
    let path = CStr::from_ptr((*info).path).to_bytes();
    path == b"/sdcard/game/iboxed trampoline.itr" || path == b"/mnt/sdcard/game/iboxed trampoline.itr"
}

#[no_mangle]
pub extern "C" fn retro_api_version() -> u32 { 1 }
#[no_mangle]
pub extern "C" fn retro_set_environment(callback: Option<Environment>) {
    apply_to_state(|state| state.environment = callback);
}
#[no_mangle]
pub extern "C" fn retro_set_video_refresh(_: Option<Video>) {}
#[no_mangle]
pub extern "C" fn retro_set_audio_sample(_: Option<Audio>) {}
#[no_mangle]
pub extern "C" fn retro_set_audio_sample_batch(_: Option<AudioBatch>) {}
#[no_mangle]
pub extern "C" fn retro_set_input_poll(_: Option<Poll>) {}
#[no_mangle]
pub extern "C" fn retro_set_input_state(_: Option<Input>) {}
#[no_mangle]
pub extern "C" fn retro_init() { apply_to_state(|state| { state.loaded = false; state.attempted = false; }); }
#[no_mangle]
pub extern "C" fn retro_deinit() { retro_unload_game(); }
/// # Safety
/// Output pointer must be writable for a libretro system_info, or null.
#[no_mangle]
pub unsafe extern "C" fn retro_get_system_info(output: *mut SystemInfo) {
    if !output.is_null() {
        output.write(SystemInfo {
            name: c"iboxed trampoline".as_ptr(), version: c"0.1.0".as_ptr(), extensions: c"itr".as_ptr(),
            fullpath: true, block_extract: true,
        });
    }
}
/// # Safety
/// Output pointer must be writable for a libretro system_av_info, or null.
#[no_mangle]
pub unsafe extern "C" fn retro_get_system_av_info(output: *mut AvInfo) {
    if !output.is_null() {
        output.write(AvInfo {
            geometry: Geometry { base_width: 320, base_height: 240, max_width: 320, max_height: 240, aspect: 4.0 / 3.0 },
            timing: Timing { fps: 60.0, sample_rate: 48000.0 },
        });
    }
}
/// # Safety
/// Pointers must satisfy the libretro game_info contract.
#[no_mangle]
pub unsafe extern "C" fn retro_load_game(info: *const GameInfo) -> bool {
    apply_to_state(|state| {
        state.loaded = has_token_path(info);
        state.attempted = false;
        state.loaded
    }).unwrap_or(false)
}
#[no_mangle]
pub extern "C" fn retro_unload_game() { apply_to_state(|state| state.loaded = false); }
#[no_mangle]
pub extern "C" fn retro_reset() {}
#[no_mangle]
pub extern "C" fn retro_run() {
    apply_to_state(|state| {
        if !state.loaded || state.attempted {
            return;
        }
        state.attempted = true;
        if launch_helper().is_err() {
            if let Some(environment) = state.environment {
                let mut message = Message { text: c"Trampoline launch failed; no automatic retry".as_ptr(), frames: 300 };
                unsafe {
                    environment(6, (&mut message as *mut Message).cast()); // SET_MESSAGE
                    environment(7, core::ptr::null_mut()); // SHUTDOWN
                }
            }
        }
    });
}
#[no_mangle]
pub extern "C" fn retro_load_game_special(_: u32, _: *const GameInfo, _: usize) -> bool { false }
#[no_mangle]
pub extern "C" fn retro_set_controller_port_device(_: u32, _: u32) {}
#[no_mangle]
pub extern "C" fn retro_get_region() -> u32 { 0 }
#[no_mangle]
pub extern "C" fn retro_serialize_size() -> usize { 0 }
#[no_mangle]
pub extern "C" fn retro_serialize(_: *mut c_void, _: usize) -> bool { false }
#[no_mangle]
pub extern "C" fn retro_unserialize(_: *const c_void, _: usize) -> bool { false }
#[no_mangle]
pub extern "C" fn retro_cheat_reset() {}
#[no_mangle]
pub extern "C" fn retro_cheat_set(_: u32, _: bool, _: *const c_char) {}
#[no_mangle]
pub extern "C" fn retro_get_memory_data(_: u32) -> *mut c_void { core::ptr::null_mut() }
#[no_mangle]
pub extern "C" fn retro_get_memory_size(_: u32) -> usize { 0 }
