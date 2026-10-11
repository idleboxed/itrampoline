use std::{env, error::Error, process::Command};

fn main() -> Result<(), Box<dyn Error>> {
    let target_environment = env::var("CARGO_CFG_TARGET_ENV")
        .map_err(|error| format!("Cannot read Cargo target environment: {error}"))?;

    if target_environment == "musl" {
        // Only compiler memory intrinsics from static musl; no vendor libc ABI.
        let rustc = env::var_os("RUSTC").ok_or("Cargo did not set RUSTC")?;
        let output = Command::new(rustc).args(["--print", "sysroot"]).output()
            .map_err(|error| format!("Cannot run rustc --print sysroot: {error}"))?;

        if !output.status.success() {
            return Err(format!("rustc --print sysroot failed ({}): {}",
                output.status, String::from_utf8_lossy(&output.stderr).trim()).into());
        }

        let root = String::from_utf8(output.stdout)
            .map_err(|error| format!("Invalid UTF-8 in rustc sysroot: {error}"))?;

        if root.trim().is_empty() {
            return Err("rustc returned an empty sysroot".into());
        }

        let target = env::var("TARGET").map_err(|error| format!("Cannot read Cargo TARGET: {error}"))?;
        println!("cargo:rustc-link-search=native={}/lib/rustlib/{target}/lib/self-contained", root.trim());
        println!("cargo:rustc-link-lib=static=c");
        println!("cargo:rustc-link-arg=--exclude-libs=ALL");
        println!("cargo:rustc-link-arg=--no-undefined");
    }

    Ok(())
}
