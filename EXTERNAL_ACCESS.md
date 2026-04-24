# Busibox External Access Guide

## 📍 Your Server Details

- **Hostname**: legion
- **LAN IP**: 192.168.68.91
- **Tailscale IP**: 100.68.175.70
- **Tailscale Status**: ✅ Active and connected

## 🌐 Access Methods

### Option 1: LAN Access (Same Network)

From any device on your 192.168.68.0/24 network:

| Service | URL |
|---------|-----|
| Agent API | http://192.168.68.91:8000/docs |
| Data API | http://192.168.68.91:8002/docs |
| Search API | http://192.168.68.91:8003/docs |
| AuthZ API | http://192.168.68.91:8010/docs |
| Deploy API | http://192.168.68.91:8011/docs |
| LiteLLM | http://192.168.68.91:4000 |
| MinIO Console | http://192.168.68.91:9001 |
| Main Portal | http://192.168.68.91 |

**Pros**: Fast, low latency
**Cons**: Only works on same network

### Option 2: Tailscale Access (Anywhere)

From any device on your Tailscale network:

| Service | URL |
|---------|-----|
| Agent API | http://100.68.175.70:8000/docs or http://legion:8000/docs |
| Data API | http://100.68.175.70:8002/docs or http://legion:8002/docs |
| Search API | http://100.68.175.70:8003/docs or http://legion:8003/docs |
| Main Portal | http://100.68.175.70 or http://legion |

**Pros**: Works from anywhere, encrypted, no firewall config needed
**Cons**: Slightly higher latency

### Your Tailscale Devices

- ✅ **legion** (this server) - 100.68.175.70
- ✅ **cos** - 100.90.104.98 (active)
- ✅ **condor3056** - 100.113.142.113
- ⚠️ **pixel-8-pro** - offline (last seen 7d ago)
- ⚠️ **rodin** - offline (last seen 24d ago)

## 🔥 Firewall Configuration

### Quick Setup (Run on server):

```bash
cd ~/maigent-code/busibox
./allow-external-access.sh
```

### Manual Setup:

```bash
# Allow HTTP/HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Allow API ports (all services)
sudo ufw allow 8000:8099/tcp

# Allow MinIO console
sudo ufw allow 9001/tcp

# Allow LiteLLM
sudo ufw allow 4000/tcp

# Check status
sudo ufw status
```

### For Tailscale Only (No Firewall Config Needed)

If you only want Tailscale access and want to block LAN access:

```bash
# Don't run the firewall script
# Tailscale traffic bypasses UFW
# Only accessible via Tailscale IPs
```

## 🧪 Testing Access

### From Another Machine

**Test LAN access:**
```bash
curl http://192.168.68.91:8000/health
```

**Test Tailscale access:**
```bash
curl http://100.68.175.70:8000/health
# or
curl http://legion:8000/health
```

**Test in browser:**
- Open http://192.168.68.91:8000/docs (LAN)
- Open http://legion:8000/docs (Tailscale)

## 🔒 Security Notes

### LAN Access
- Anyone on your network can access
- No authentication by default
- Use AuthZ API for proper authentication

### Tailscale Access
- Only devices in your Tailnet can access
- Traffic is encrypted
- More secure than LAN-only
- Can use Tailscale ACLs for fine-grained control

## 🚀 Recommended Setup

**For personal use:**
- Use Tailscale for remote access (most secure)
- Allow LAN for local devices (convenience)
- Enable firewall rules

**For team use:**
- Use Tailscale exclusively
- Set up Tailscale ACLs to control access
- Enable AuthZ authentication
- Use HTTPS with proper certificates

## 📱 Mobile Access

From your **pixel-8-pro** (when online):
1. Connect to Tailscale
2. Open browser
3. Go to http://legion:8000/docs
4. Bookmark your favorite APIs!

## 💡 Tips

**Use hostname instead of IP:**
```
http://legion:8000/docs  ✅ Easy to remember
http://100.68.175.70:8000/docs  ⚠️ Hard to remember
```

**Create bookmarks:**
- Save all these URLs in a bookmark folder
- Access Busibox from any device instantly

**Share with team:**
- Add team members to your Tailscale network
- They can access http://legion:8000/docs
- No VPN or port forwarding needed!

## 🔄 After Reboot

Everything will automatically start:
- ✅ Docker containers auto-start
- ✅ Tailscale auto-connects
- ✅ Firewall rules persist
- ✅ All URLs work immediately

---

**Last Updated**: 2026-03-27
**Configuration**: nginx updated, Tailscale active, firewall script created
