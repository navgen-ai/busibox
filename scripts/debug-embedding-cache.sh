#!/bin/bash
#
# Debug Embedding Model Cache
# Run this on Proxmox host to diagnose cache issues

echo "=========================================="
echo "Embedding Model Cache Diagnostics"
echo "=========================================="
echo ""

echo "1. ZFS Datasets:"
echo "----------------"
zfs list -o name,used,mounted,mountpoint | grep embedding || echo "No embedding datasets found"
echo ""

echo "2. Directory Structure:"
echo "----------------------"
ls -laR /var/lib/embedding-models/ 2>/dev/null || echo "Directory /var/lib/embedding-models/ does not exist"
echo ""

echo "3. Expected Cache Location:"
echo "--------------------------"
FASTEMBED_CACHE="/var/lib/embedding-models/fastembed"
echo "Cache directory: ${FASTEMBED_CACHE}"
if [[ -d "${FASTEMBED_CACHE}" ]]; then
    echo "✓ Directory exists"
    echo "Contents:"
    ls -la "${FASTEMBED_CACHE}"
else
    echo "✗ Directory does NOT exist"
fi
echo ""

echo "4. Check for Models:"
echo "-------------------"
if [[ -d "${FASTEMBED_CACHE}" ]]; then
    find "${FASTEMBED_CACHE}" -type f -name "*.onnx" | head -10
    model_count=$(find "${FASTEMBED_CACHE}" -type f -name "*.onnx" 2>/dev/null | wc -l)
    echo "Found ${model_count} .onnx files"
else
    echo "Cannot check - cache directory missing"
fi
