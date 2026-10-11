fn main() -> std::process::ExitCode {
    // Never print to inherited stdio: the frontend may have pointed it at SD.
    if unsafe { itrampoline_helper::sanitize_inherited() }.is_err()
        || itrampoline_helper::launch().is_err()
    {
        return std::process::ExitCode::from(70);
    }
    std::process::ExitCode::SUCCESS
}
