use aes_gcm::{
    aead::{Aead, AeadCore, KeyInit, OsRng},
    Aes256Gcm, Key, Nonce,
};
use argon2::Argon2;
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use color_eyre::{eyre::eyre, Result};
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncryptedVault {
    pub version: u32,
    pub salt: String,
    pub nonce: String,
    pub ciphertext: String,
}

fn derive_key(master_password: &str, salt: &[u8]) -> Result<Key<Aes256Gcm>> {
    let mut key_bytes = [0u8; 32];
    Argon2::default()
        .hash_password_into(master_password.as_bytes(), salt, &mut key_bytes)
        .map_err(|e| eyre!("Key derivation failed: {e}"))?;
    Ok(*Key::<Aes256Gcm>::from_slice(&key_bytes))
}

/// Generate a cryptographically random vault password (32 alphanumeric chars).
pub fn generate_vault_password() -> String {
    const CHARSET: &[u8] = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    let mut rng = rand::thread_rng();
    (0..32)
        .map(|_| {
            let idx = rng.gen_range(0..CHARSET.len());
            CHARSET[idx] as char
        })
        .collect()
}

/// Encrypt a vault password with a master password.
pub fn encrypt_vault_password(vault_password: &str, master_password: &str) -> Result<EncryptedVault> {
    let mut salt = [0u8; 32];
    OsRng.fill_bytes(&mut salt);

    let key = derive_key(master_password, &salt)?;
    let cipher = Aes256Gcm::new(&key);
    let nonce = Aes256Gcm::generate_nonce(&mut OsRng);

    let ciphertext = cipher
        .encrypt(&nonce, vault_password.as_bytes())
        .map_err(|e| eyre!("Encryption failed: {e}"))?;

    Ok(EncryptedVault {
        version: 1,
        salt: B64.encode(salt),
        nonce: B64.encode(nonce),
        ciphertext: B64.encode(ciphertext),
    })
}

/// Decrypt a vault password using a master password.
pub fn decrypt_vault_password(enc: &EncryptedVault, master_password: &str) -> Result<String> {
    if enc.version != 1 {
        return Err(eyre!("Unsupported vault key version: {}", enc.version));
    }

    let salt = B64
        .decode(&enc.salt)
        .map_err(|e| eyre!("Invalid salt: {e}"))?;
    let nonce_bytes = B64
        .decode(&enc.nonce)
        .map_err(|e| eyre!("Invalid nonce: {e}"))?;
    let ciphertext = B64
        .decode(&enc.ciphertext)
        .map_err(|e| eyre!("Invalid ciphertext: {e}"))?;

    let key = derive_key(master_password, &salt)?;
    let cipher = Aes256Gcm::new(&key);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let plaintext = cipher
        .decrypt(nonce, ciphertext.as_ref())
        .map_err(|_| eyre!("Decryption failed — wrong master password?"))?;

    String::from_utf8(plaintext).map_err(|e| eyre!("Vault password is not valid UTF-8: {e}"))
}

/// Save an encrypted vault to a JSON file using atomic write (temp + rename).
pub fn save_encrypted_vault(path: &Path, enc: &EncryptedVault) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let json = serde_json::to_string_pretty(enc)?;
    let tmp = path.with_extension("enc.tmp");
    std::fs::write(&tmp, format!("{json}\n"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
    }
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// Load an encrypted vault from a JSON file.
pub fn load_encrypted_vault(path: &Path) -> Result<EncryptedVault> {
    let contents = std::fs::read_to_string(path)
        .map_err(|e| eyre!("Cannot read vault key file {}: {e}", path.display()))?;
    let enc: EncryptedVault = serde_json::from_str(&contents)
        .map_err(|e| eyre!("Invalid vault key file {}: {e}", path.display()))?;
    Ok(enc)
}

/// Return the vault-keys directory: ~/.busibox/vault-keys/
pub fn vault_keys_dir() -> Result<PathBuf> {
    let home = dirs::home_dir().ok_or_else(|| eyre!("Cannot determine home directory"))?;
    Ok(home.join(".busibox").join("vault-keys"))
}

/// Return the path to the encrypted vault key for a profile.
/// e.g. ~/.busibox/vault-keys/{profile_id}.enc
pub fn vault_key_path(profile_id: &str) -> Result<PathBuf> {
    Ok(vault_keys_dir()?.join(format!("{profile_id}.enc")))
}

/// Check if an encrypted vault key exists for a profile.
pub fn has_vault_key(profile_id: &str) -> bool {
    vault_key_path(profile_id)
        .map(|p| p.exists())
        .unwrap_or(false)
}

/// Check if a legacy plaintext vault password file exists for a given prefix.
/// Returns the path if found.
pub fn find_legacy_vault_pass(prefix: &str) -> Option<PathBuf> {
    let home = dirs::home_dir()?;
    let path = home.join(format!(".busibox-vault-pass-{prefix}"));
    if path.exists() {
        Some(path)
    } else {
        let legacy = home.join(".vault_pass");
        if legacy.exists() {
            Some(legacy)
        } else {
            None
        }
    }
}

/// Prompt the user for a password (hidden input). Must be called outside raw mode.
pub fn prompt_password(prompt: &str) -> Result<String> {
    let password = rpassword::prompt_password(prompt)
        .map_err(|e| eyre!("Failed to read password: {e}"))?;
    Ok(password)
}

/// Prompt for a new password with confirmation. Must be called outside raw mode.
pub fn prompt_new_password(prompt: &str) -> Result<String> {
    loop {
        let p1 = prompt_password(prompt)?;
        if p1.is_empty() {
            eprintln!("Password cannot be empty.");
            continue;
        }
        let p2 = prompt_password("Confirm password: ")?;
        if p1 != p2 {
            eprintln!("Passwords do not match. Try again.");
            continue;
        }
        return Ok(p1);
    }
}

use aes_gcm::aead::rand_core::RngCore;

// ============================================================================
// Vault file resolution (ported from scripts/lib/vault.sh)
// ============================================================================

/// Return the vault file path for a given prefix (profile ID or env prefix).
/// e.g., `vault_file_path(repo_root, "my-profile")` → `.../vault.my-profile.yml`
pub fn vault_file_path(repo_root: &Path, prefix: &str) -> PathBuf {
    repo_root
        .join("provision/ansible/roles/secrets/vars")
        .join(format!("vault.{prefix}.yml"))
}

/// Return the vault example file path.
pub fn vault_example_path(repo_root: &Path) -> PathBuf {
    repo_root
        .join("provision/ansible/roles/secrets/vars")
        .join("vault.example.yml")
}

/// Check if a vault file exists for a given prefix.
pub fn has_vault_file(repo_root: &Path, prefix: &str) -> bool {
    vault_file_path(repo_root, prefix).exists()
}

/// Find the legacy plaintext vault password file for a prefix.
/// Checks `BUSIBOX_VAULT_PASS_DIR` first, then `$HOME`, then legacy `~/.vault_pass`.
pub fn find_vault_pass_file(prefix: &str) -> Option<PathBuf> {
    let home = dirs::home_dir()?;

    // Check standard location
    let standard = home.join(format!(".busibox-vault-pass-{prefix}"));
    if standard.exists() {
        return Some(standard);
    }

    // Check legacy universal path
    let legacy = home.join(".vault_pass");
    if legacy.exists() {
        return Some(legacy);
    }

    None
}

/// Verify that a vault file can be decrypted with a given password.
/// Uses `ansible-vault view` under the hood.
pub fn verify_vault_decryption(vault_file: &Path, password: &str) -> Result<bool> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    if !vault_file.exists() {
        return Err(eyre!("Vault file not found: {}", vault_file.display()));
    }

    let mut child = Command::new("ansible-vault")
        .args(["view", &vault_file.to_string_lossy()])
        .arg("--vault-password-file=/dev/stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| eyre!("Failed to run ansible-vault: {e}"))?;

    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(password.as_bytes());
    }

    let status = child.wait()?;
    Ok(status.success())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn generate_vault_password_has_correct_length() {
        let pw = generate_vault_password();
        assert_eq!(pw.len(), 32);
    }

    #[test]
    fn generate_vault_password_is_alphanumeric() {
        let pw = generate_vault_password();
        assert!(pw.chars().all(|c| c.is_ascii_alphanumeric()));
    }

    #[test]
    fn generate_vault_password_is_random() {
        let pw1 = generate_vault_password();
        let pw2 = generate_vault_password();
        assert_ne!(pw1, pw2, "Two generated passwords should differ");
    }

    #[test]
    fn encrypt_decrypt_round_trip() {
        let vault_pw = "test-vault-password-12345";
        let master_pw = "master-secret";

        let encrypted = encrypt_vault_password(vault_pw, master_pw).unwrap();
        let decrypted = decrypt_vault_password(&encrypted, master_pw).unwrap();

        assert_eq!(decrypted, vault_pw);
    }

    #[test]
    fn encrypt_decrypt_with_special_chars() {
        let vault_pw = "p@$$w0rd!#%^&*()_+-=[]{}|;':\",./<>?";
        let master_pw = "müñ!çh€n🔑";

        let encrypted = encrypt_vault_password(vault_pw, master_pw).unwrap();
        let decrypted = decrypt_vault_password(&encrypted, master_pw).unwrap();

        assert_eq!(decrypted, vault_pw);
    }

    #[test]
    fn decrypt_with_wrong_password_fails() {
        let vault_pw = "test-vault-password";
        let master_pw = "correct-password";
        let wrong_pw = "wrong-password";

        let encrypted = encrypt_vault_password(vault_pw, master_pw).unwrap();
        let result = decrypt_vault_password(&encrypted, wrong_pw);

        assert!(result.is_err());
    }

    #[test]
    fn encrypted_vault_has_version_1() {
        let encrypted = encrypt_vault_password("pw", "master").unwrap();
        assert_eq!(encrypted.version, 1);
    }

    #[test]
    fn encrypted_vault_fields_are_base64() {
        let encrypted = encrypt_vault_password("pw", "master").unwrap();
        assert!(base64::engine::general_purpose::STANDARD.decode(&encrypted.salt).is_ok());
        assert!(base64::engine::general_purpose::STANDARD.decode(&encrypted.nonce).is_ok());
        assert!(base64::engine::general_purpose::STANDARD.decode(&encrypted.ciphertext).is_ok());
    }

    #[test]
    fn decrypt_unsupported_version_fails() {
        let mut encrypted = encrypt_vault_password("pw", "master").unwrap();
        encrypted.version = 99;
        let result = decrypt_vault_password(&encrypted, "master");
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("Unsupported vault key version"));
    }

    #[test]
    fn save_and_load_encrypted_vault_round_trip() {
        let tmp = std::env::temp_dir().join("busibox-test-vault.enc");
        let encrypted = encrypt_vault_password("vault-pw", "master-pw").unwrap();

        save_encrypted_vault(&tmp, &encrypted).unwrap();
        let loaded = load_encrypted_vault(&tmp).unwrap();

        assert_eq!(loaded.version, encrypted.version);
        assert_eq!(loaded.salt, encrypted.salt);
        assert_eq!(loaded.nonce, encrypted.nonce);
        assert_eq!(loaded.ciphertext, encrypted.ciphertext);

        let decrypted = decrypt_vault_password(&loaded, "master-pw").unwrap();
        assert_eq!(decrypted, "vault-pw");

        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn load_encrypted_vault_missing_file_fails() {
        let result = load_encrypted_vault(Path::new("/nonexistent/path.enc"));
        assert!(result.is_err());
    }

    #[test]
    fn vault_file_path_builds_correct_path() {
        let repo = PathBuf::from("/home/user/busibox");
        let path = vault_file_path(&repo, "my-profile");
        assert_eq!(
            path,
            PathBuf::from("/home/user/busibox/provision/ansible/roles/secrets/vars/vault.my-profile.yml")
        );
    }

    #[test]
    fn vault_example_path_builds_correct_path() {
        let repo = PathBuf::from("/home/user/busibox");
        let path = vault_example_path(&repo);
        assert_eq!(
            path,
            PathBuf::from("/home/user/busibox/provision/ansible/roles/secrets/vars/vault.example.yml")
        );
    }

    #[test]
    fn vault_key_path_builds_correct_path() {
        let path = vault_key_path("my-profile").unwrap();
        let home = dirs::home_dir().unwrap();
        assert_eq!(path, home.join(".busibox/vault-keys/my-profile.enc"));
    }

    #[test]
    fn vault_keys_dir_is_under_home() {
        let dir = vault_keys_dir().unwrap();
        let home = dirs::home_dir().unwrap();
        assert!(dir.starts_with(&home));
        assert!(dir.ends_with("vault-keys"));
    }
}

/// Create a new vault file from the example template, then encrypt it.
pub fn create_vault_from_example(repo_root: &Path, prefix: &str, password: &str) -> Result<()> {
    let example = vault_example_path(repo_root);
    let target = vault_file_path(repo_root, prefix);

    if !example.exists() {
        return Err(eyre!("Vault example not found: {}", example.display()));
    }

    std::fs::copy(&example, &target)?;

    use std::io::Write;
    use std::process::{Command, Stdio};

    let mut child = Command::new("ansible-vault")
        .args(["encrypt", &target.to_string_lossy()])
        .arg("--vault-password-file=/dev/stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| eyre!("Failed to run ansible-vault encrypt: {e}"))?;

    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(password.as_bytes());
    }

    let status = child.wait()?;
    if !status.success() {
        return Err(eyre!("ansible-vault encrypt failed"));
    }

    Ok(())
}
