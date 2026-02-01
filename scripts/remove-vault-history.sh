#!/usr/bin/env bash
#
# Remove Vault Files from Git History
# ====================================
#
# This script removes sensitive vault files from the entire Git history.
# 
# WARNING: This is a DESTRUCTIVE operation that:
# - Rewrites Git history
# - Requires force-pushing to remote
# - Requires all collaborators to re-clone the repository
#
# Files to remove:
# - provision/ansible/roles/secrets/vars/vault.yml
# - provision/ansible/roles/secrets/vars/vault.prod.yml
# - provision/ansible/roles/secrets/vars/vault.yml.cashman
# - provision/ansible/roles/secrets/vars/vault.yml.new
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║          REMOVE VAULT FILES FROM GIT HISTORY                         ║${NC}"
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo ""

# Files to remove from history
FILES=(
    "provision/ansible/roles/secrets/vars/vault.yml"
    "provision/ansible/roles/secrets/vars/vault.prod.yml"
    "provision/ansible/roles/secrets/vars/vault.yml.cashman"
    "provision/ansible/roles/secrets/vars/vault.yml.new"
)

echo -e "${YELLOW}WARNING: This script will:${NC}"
echo "  1. Rewrite the entire Git history"
echo "  2. Remove the following files from all commits:"
for file in "${FILES[@]}"; do
    echo "     - $file"
done
echo "  3. Require force-pushing to remote"
echo "  4. Require all collaborators to re-clone the repository"
echo ""
echo -e "${RED}This operation CANNOT be undone!${NC}"
echo ""

# Check if git-filter-repo is installed
if ! command -v git-filter-repo &>/dev/null; then
    echo -e "${YELLOW}git-filter-repo is not installed.${NC}"
    echo ""
    echo "Installing git-filter-repo..."
    
    # Try to install with pip
    if command -v pip3 &>/dev/null; then
        pip3 install git-filter-repo
    elif command -v pip &>/dev/null; then
        pip install git-filter-repo
    else
        echo -e "${RED}Error: pip/pip3 not found. Please install git-filter-repo manually:${NC}"
        echo "  pip3 install git-filter-repo"
        echo "  OR"
        echo "  brew install git-filter-repo"
        exit 1
    fi
fi

# Verify installation
if ! command -v git-filter-repo &>/dev/null; then
    echo -e "${RED}Error: git-filter-repo installation failed${NC}"
    exit 1
fi

echo -e "${GREEN}✓ git-filter-repo is installed${NC}"
echo ""

# Confirmation
read -p "Do you want to proceed? (type 'yes' to confirm): " confirm
if [[ "$confirm" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo -e "${CYAN}Step 1: Creating backup of current repository${NC}"
cd "$REPO_ROOT"

BACKUP_DIR="${REPO_ROOT}/../busibox-backup-$(date +%Y%m%d-%H%M%S)"
echo "Creating backup at: $BACKUP_DIR"
cp -r "$REPO_ROOT" "$BACKUP_DIR"
echo -e "${GREEN}✓ Backup created${NC}"
echo ""

echo -e "${CYAN}Step 2: Checking for uncommitted changes${NC}"
if [[ -n $(git status --porcelain) ]]; then
    echo -e "${RED}Error: You have uncommitted changes. Please commit or stash them first.${NC}"
    git status
    exit 1
fi
echo -e "${GREEN}✓ No uncommitted changes${NC}"
echo ""

echo -e "${CYAN}Step 3: Fetching all branches from remote${NC}"
git fetch --all
echo -e "${GREEN}✓ Fetched all branches${NC}"
echo ""

echo -e "${CYAN}Step 4: Removing files from history${NC}"
echo "This may take a few minutes..."

# Build the paths argument for git-filter-repo
paths_args=""
for file in "${FILES[@]}"; do
    paths_args="$paths_args --path $file"
done

# Use git-filter-repo to remove the files
git filter-repo --invert-paths $paths_args --force

echo -e "${GREEN}✓ Files removed from history${NC}"
echo ""

echo -e "${CYAN}Step 5: Verifying removal${NC}"
for file in "${FILES[@]}"; do
    if git log --all --full-history --oneline -- "$file" 2>/dev/null | grep -q .; then
        echo -e "${RED}✗ File still in history: $file${NC}"
    else
        echo -e "${GREEN}✓ Removed: $file${NC}"
    fi
done
echo ""

echo -e "${CYAN}Step 6: Adding files to .gitignore${NC}"
GITIGNORE="${REPO_ROOT}/.gitignore"

for file in "${FILES[@]}"; do
    filename=$(basename "$file")
    if ! grep -q "^${filename}$" "$GITIGNORE" 2>/dev/null; then
        echo "$filename" >> "$GITIGNORE"
        echo "  Added: $filename"
    else
        echo "  Already in .gitignore: $filename"
    fi
done
echo -e "${GREEN}✓ .gitignore updated${NC}"
echo ""

# Stage .gitignore changes if any
if [[ -n $(git status --porcelain .gitignore) ]]; then
    git add .gitignore
    git commit -m "chore: add vault files to .gitignore"
    echo -e "${GREEN}✓ Committed .gitignore changes${NC}"
    echo ""
fi

echo -e "${CYAN}Step 7: Repository statistics${NC}"
echo "Repository size:"
du -sh .git
echo ""

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    HISTORY REWRITE COMPLETE                          ║${NC}"
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo ""

echo -e "${YELLOW}NEXT STEPS:${NC}"
echo ""
echo "1. Verify the changes locally:"
echo "   git log --all --oneline -- provision/ansible/roles/secrets/vars/"
echo ""
echo "2. Force-push to remote (DESTRUCTIVE - cannot undo):"
echo "   git push origin --force --all"
echo "   git push origin --force --tags"
echo ""
echo "3. Notify all collaborators to:"
echo "   - Save any uncommitted work"
echo "   - Delete their local clone"
echo "   - Re-clone the repository"
echo ""
echo "4. If you have a fork, update it:"
echo "   git push fork --force --all"
echo "   git push fork --force --tags"
echo ""
echo -e "${YELLOW}Backup location: ${BACKUP_DIR}${NC}"
echo ""
echo -e "${CYAN}If something went wrong, restore from backup:${NC}"
echo "  rm -rf ${REPO_ROOT}/.git"
echo "  cp -r ${BACKUP_DIR}/.git ${REPO_ROOT}/"
echo ""
