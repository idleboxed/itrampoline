use itrampoline_core::*;
use std::{ffi::CStr, ptr};

#[test]
fn libretro_metadata_and_content_selection_are_minimal() {
    assert_eq!(retro_api_version(), 1);
    let mut info = SystemInfo { name: ptr::null(), version: ptr::null(), extensions: ptr::null(), fullpath: false, block_extract: false };
    unsafe { retro_get_system_info(&mut info); }
    assert_eq!(unsafe { CStr::from_ptr(info.name) }, c"iboxed trampoline");
    assert_eq!(unsafe { CStr::from_ptr(info.extensions) }, c"itr");
    assert!(info.fullpath && info.block_extract);
    retro_init();
    assert!(!unsafe { retro_load_game(ptr::null()) });
    let mut game = GameInfo { path: c"/sdcard/game/actual-rom.itr".as_ptr(), data: ptr::null(), size: 0, meta: ptr::null() };
    assert!(!unsafe { retro_load_game(&game) });
    game.path = c"/sdcard/game/iboxed trampoline.itr".as_ptr();
    assert!(unsafe { retro_load_game(&game) });
    retro_unload_game();
    retro_run(); // Unloaded core cannot inspect hardware or execute anything.
    assert_eq!(retro_serialize_size(), 0);
    assert!(retro_get_memory_data(0).is_null());
    retro_deinit();
}
