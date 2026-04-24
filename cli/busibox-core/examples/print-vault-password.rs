//! Decrypt a busibox vault key with a master password and print the vault password.
//!
//! Usage:
//!   echo <master-password> | cargo run --example print-vault-password -- <profile-id>
//!
//! Helper for scripting/debugging only — avoids requiring the full TUI.

use busibox_core::vault;
use std::io::{self, Read};

fn main() -> color_eyre::Result<()> {
    color_eyre::install()?;

    let profile_id = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "local-development-docker".to_string());

    let mut master = String::new();
    io::stdin().read_to_string(&mut master)?;
    let master = master.trim();
    if master.is_empty() {
        eprintln!("error: master password required on stdin");
        std::process::exit(2);
    }

    let key_path = vault::vault_key_path(&profile_id)?;
    let enc = vault::load_encrypted_vault(&key_path)?;
    let vault_password = vault::decrypt_vault_password(&enc, master)?;
    print!("{vault_password}");
    Ok(())
}
