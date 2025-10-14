## Quickstart on Proxmox

1) On the Proxmox host:
```bash
cd /root
# copy repo here; then
cd /root/busibox/provision/pct
vim vars.env   # adjust CTIDs, IPs, template, storage
bash create_lxc_base.sh
```

2) On your admin workstation (with Ansible):
```bash
cd provision/ansible
# adjust inventory/hosts.yml to the IPs you set
make all
```

3) Access:
- MinIO Console: http://10.96.200.21:9001  (change credentials in /srv/minio/.env)
- Postgres: 10.96.200.22:5432 (user: appuser / change password in role)
- Milvus: 10.96.200.23:19530
- Agent API: http://10.96.200.24:3001/health
- Ingest worker: systemd logs

4) Next steps:
- Replace placeholder agent/ingest apps with your real code.
- Wire MinIO bucket notifications to agent webhook.
- Run `tools/milvus_init.py` from any box with `pymilvus` installed.
```bash
pip install pymilvus
python tools/milvus_init.py
```
