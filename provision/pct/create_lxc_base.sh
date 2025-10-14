#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/vars.env"

create_ct () {
  local CTID=$1 IP=$2 NAME=$3 PRIV=$4
  echo "==> Creating $NAME ($CTID) at $IP"
  pct create "$CTID" "$TEMPLATE"     -hostname "$NAME"     -net0 name=eth0,bridge=$BRIDGE,ip=${IP}${CIDR},gw=$GW     -storage "$STORAGE"     -memory "$MEM_MB" -cores "$CPUS"     -rootfs "$STORAGE:$DISK_GB"     -features nesting=1,keyctl=1     -onboot 1 -start 1     -ssh-public-keys "$SSH_PUBKEY_PATH"
  if [[ "$PRIV" == "priv" ]]; then
    pct set "$CTID" -unprivileged 0
    pct set "$CTID" -features nesting=1,keyctl=1
  fi
  # /dev/net/tun
  echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> "/etc/pve/lxc/${CTID}.conf"
  echo "lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file" >> "/etc/pve/lxc/${CTID}.conf"
  pct start "$CTID"
  sleep 3
  pct exec "$CTID" -- bash -lc "apt update && apt install -y curl ca-certificates sudo gnupg ufw"
}

create_ct "$CT_FILES"  "$IP_FILES"  files-lxc  priv
create_ct "$CT_PG"     "$IP_PG"     pg-lxc     unpriv
create_ct "$CT_MILVUS" "$IP_MILVUS" milvus-lxc priv
create_ct "$CT_AGENT"  "$IP_AGENT"  agent-lxc  unpriv
create_ct "$CT_INGEST" "$IP_INGEST" ingest-lxc unpriv

echo "All containers created."
