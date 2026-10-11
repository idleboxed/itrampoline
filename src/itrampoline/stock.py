from dataclasses import dataclass


@dataclass(frozen=True)
class CoreSlot:
    type_id: int
    filename: str
    platform: str


# Subjective niche-to-mainstream order, not usage measurements or empty slots.
SLOTS = (
    CoreSlot(2, "daphne_libretro.so", "Daphne / LaserDisc arcade"),
    CoreSlot(17, "prosystem_libretro.so", "Atari 7800"),
    CoreSlot(16, "atari800_libretro.so", "Atari 5200"),
    CoreSlot(11, "mednafen_wswan_libretro.so", "WonderSwan"),
    CoreSlot(15, "stella_libretro.so", "Atari 2600"),
    CoreSlot(10, "mednafen_pce_fast_libretro.so", "PC Engine"),
    CoreSlot(13, "genesis_plus_gx_libretro.so", "Master System / Game Gear"),
    CoreSlot(12, "desmume2015_libretro.so", "Nintendo DS"),
    CoreSlot(18, "mame2016_libretro.so", "Arcade / MAME 2016"),
    CoreSlot(3, "fbalpha2012_libretro1.so", "Arcade / FBA 2012 variant"),
    CoreSlot(14, "fbalpha_libretro.so", "Arcade / FBA"),
    CoreSlot(4, "mame2003_libretro.so", "Arcade / MAME 2003"),
    CoreSlot(0, "fbalpha2012_libretro.so", "Arcade / FBA 2012"),
    CoreSlot(8, "mupen64plus_libretro.so", "Nintendo 64"),
    CoreSlot(6, "snes9x_libretro.so", "Super Nintendo"),
    CoreSlot(7, "mgba_libretro.so", "Game Boy / Color / Advance"),
    CoreSlot(5, "genesisplusgx_libretro.so", "Mega Drive"),
    CoreSlot(9, "pcsx_rearmed_libretro.so", "PlayStation"),
    CoreSlot(1, "nestopia_libretro.so", "NES / Famicom"),
)
DEFAULT_SLOT = SLOTS[0]
TITLE = "iboxed trampoline"
TOKEN_PATH = "game/iboxed trampoline.itr"
TOKEN_CONTENT = b"system/iman/iman\n"
HELPER_PATH = "system/itrampoline"
DATABASE_PATH = "game/games.db"
