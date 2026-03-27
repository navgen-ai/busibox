#!/usr/bin/env bash
set -euo pipefail

# Temporary diagnostic script: decrypts all vault keys with a master password,
# then tries each decrypted vault password against each vault YAML file to
# produce a mapping of which key file decrypts which vault file.
#
# Usage: bash scripts/vault-key-matcher.sh
# Requires: ansible-vault, python3

VAULT_KEYS_DIR="$HOME/.busibox/vault-keys"
VAULT_DIR="$(cd "$(dirname "$0")/../provision/ansible/roles/secrets/vars" && pwd)"
BACKUP_DIR="$VAULT_DIR/backups"

echo "═══════════════════════════════════════════════════════════════"
echo "  VAULT KEY ↔ VAULT FILE MATCHER"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Key directory:   $VAULT_KEYS_DIR"
echo "Vault directory: $VAULT_DIR"
echo "Backup directory: $BACKUP_DIR"
echo ""

read -r -s -p "Master password: " MASTER_PW
echo ""
echo ""

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

python3 -m venv "$TMPDIR/venv" 2>/dev/null
source "$TMPDIR/venv/bin/activate"
pip install -q argon2-cffi pycryptodome 2>/dev/null

cat > "$TMPDIR/decrypt_key.py" << 'PYEOF'
import sys, json, base64
from argon2.low_level import hash_secret_raw, Type
from Crypto.Cipher import AES

def decrypt_vault_key(enc_file, master_password):
    with open(enc_file, 'r') as f:
        data = json.load(f)
    if data.get('version') != 1:
        return None
    salt = base64.b64decode(data['salt'])
    nonce = base64.b64decode(data['nonce'])
    ciphertext = base64.b64decode(data['ciphertext'])

    # Rust argon2 0.5 Argon2::default() = Argon2id, t_cost=2, m_cost=19456, p=1
    key = hash_secret_raw(
        secret=master_password.encode('utf-8'),
        salt=salt, time_cost=2, memory_cost=19456,
        parallelism=1, hash_len=32, type=Type.ID,
    )
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    if len(ciphertext) <= 16:
        return None
    ct, tag = ciphertext[:-16], ciphertext[-16:]
    try:
        plaintext = cipher.decrypt_and_verify(ct, tag)
        return plaintext.decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return None

if __name__ == '__main__':
    result = decrypt_vault_key(sys.argv[1], sys.argv[2])
    if result is not None:
        print(result)
    else:
        sys.exit(1)
PYEOF

echo "Phase 1: Decrypting vault key files..."
echo "─────────────────────────────────────────────────────────────"

KEY_NAMES_FILE="$TMPDIR/key_names.txt"
> "$KEY_NAMES_FILE"

for enc_file in "$VAULT_KEYS_DIR"/*.enc "$VAULT_KEYS_DIR"/*.enc.alt; do
    [ -f "$enc_file" ] || continue
    bn=$(basename "$enc_file")

    vault_pw=$(python3 "$TMPDIR/decrypt_key.py" "$enc_file" "$MASTER_PW" 2>/dev/null) || vault_pw=""

    if [ -n "$vault_pw" ]; then
        echo "  ✓ $bn → decrypted (pw: ${vault_pw:0:8}...)"
        echo "$bn" >> "$KEY_NAMES_FILE"
        echo -n "$vault_pw" > "$TMPDIR/pw_$bn"
    else
        echo "  ✗ $bn → FAILED to decrypt"
    fi
done

KEY_COUNT=$(wc -l < "$KEY_NAMES_FILE" | tr -d ' ')
echo ""
echo "Decrypted $KEY_COUNT key(s) out of $(ls "$VAULT_KEYS_DIR"/*.enc "$VAULT_KEYS_DIR"/*.enc.alt 2>/dev/null | wc -l | tr -d ' ') total."
echo ""

if [ "$KEY_COUNT" -eq 0 ]; then
    echo "No keys could be decrypted. Wrong master password?"
    exit 1
fi

echo "Continue to Phase 2 (match against vault files)? [Y/n] "
read -r answer
if [ "${answer:-y}" = "n" ] || [ "${answer:-y}" = "N" ]; then
    echo "Stopping after Phase 1."
    exit 0
fi

echo ""
echo "Phase 2: Matching keys to vault YAML files..."
echo "═══════════════════════════════════════════════════════════════"
echo ""

VAULT_FILES_LIST="$TMPDIR/vault_files.txt"
> "$VAULT_FILES_LIST"

for vf in "$VAULT_DIR"/vault.*.yml; do
    [ -f "$vf" ] || continue
    bn=$(basename "$vf")
    [ "$bn" = "vault.example.yml" ] && continue
    echo "$vf" >> "$VAULT_FILES_LIST"
done
for vf in "$BACKUP_DIR"/vault.*.yml; do
    [ -f "$vf" ] || continue
    echo "$vf" >> "$VAULT_FILES_LIST"
done

while IFS= read -r vf; do
    [ -f "$vf" ] || continue
    vf_name=$(basename "$vf")
    vf_dir=$(dirname "$vf")
    if [ "$vf_dir" = "$BACKUP_DIR" ]; then
        label="backups/$vf_name"
    else
        label="$vf_name"
    fi

    matches=""
    while IFS= read -r key_name; do
        pw_file="$TMPDIR/pw_$key_name"
        [ -f "$pw_file" ] || continue
        vault_pw=$(cat "$pw_file")
        echo -n "$vault_pw" > "$TMPDIR/try_pw"

        if ansible-vault view "$vf" --vault-password-file "$TMPDIR/try_pw" > /dev/null 2>&1; then
            short_pw="${vault_pw:0:8}..."
            matches="${matches}    ← ${key_name} (pw: ${short_pw})"$'\n'
        fi
    done < "$KEY_NAMES_FILE"

    echo "  $label"
    if [ -n "$matches" ]; then
        echo "$matches"
    else
        echo "    ← NO MATCHING KEY ⚠️"
        echo ""
    fi
done < "$VAULT_FILES_LIST"

echo "═══════════════════════════════════════════════════════════════"
echo "  KEY SUMMARY"
echo "═══════════════════════════════════════════════════════════════"
echo ""

while IFS= read -r key_name; do
    pw_file="$TMPDIR/pw_$key_name"
    [ -f "$pw_file" ] || continue
    vault_pw=$(cat "$pw_file")
    echo -n "$vault_pw" > "$TMPDIR/try_pw"

    matched=""
    while IFS= read -r vf; do
        [ -f "$vf" ] || continue
        if ansible-vault view "$vf" --vault-password-file "$TMPDIR/try_pw" > /dev/null 2>&1; then
            matched="${matched} $(basename "$vf")"
        fi
    done < "$VAULT_FILES_LIST"

    if [ -n "$matched" ]; then
        echo "  $key_name →$matched"
    else
        echo "  $key_name → ⚠️  ORPHAN (no vault file matches)"
    fi
done < "$KEY_NAMES_FILE"

echo ""
