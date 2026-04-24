#!/bin/bash
#
# Allow external access to Busibox services
#

echo "Configuring firewall for Busibox external access..."

# Allow HTTP/HTTPS
sudo ufw allow 80/tcp comment "Busibox HTTP"
sudo ufw allow 443/tcp comment "Busibox HTTPS"

# Allow API ports
sudo ufw allow 8000/tcp comment "Busibox Agent API"
sudo ufw allow 8002/tcp comment "Busibox Data API"
sudo ufw allow 8003/tcp comment "Busibox Search API"
sudo ufw allow 8004/tcp comment "Busibox Docs API"
sudo ufw allow 8005/tcp comment "Busibox Embedding API"
sudo ufw allow 8010/tcp comment "Busibox AuthZ API"
sudo ufw allow 8011/tcp comment "Busibox Deploy API"
sudo ufw allow 8012/tcp comment "Busibox Config API"

# Allow MinIO console
sudo ufw allow 9001/tcp comment "MinIO Console"

# Allow LiteLLM
sudo ufw allow 4000/tcp comment "LiteLLM Gateway"

echo ""
echo "✓ Firewall rules added!"
echo ""
echo "Current status:"
sudo ufw status numbered

echo ""
echo "To enable firewall (if not already enabled):"
echo "  sudo ufw enable"
