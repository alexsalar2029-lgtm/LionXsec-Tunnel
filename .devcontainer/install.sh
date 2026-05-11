#!/bin/sh

echo "[LionXsec] Downloading Xray Core..."
wget -q -O /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip

echo "[LionXsec] Installing Xray..."
cd /tmp && unzip -q xray.zip && chmod +x xray && mv xray /usr/local/bin/xray
rm -rf /tmp/xray.zip /tmp/geo* /tmp/LICENSE /tmp/README.md

echo "[LionXsec] Setting up keepalive..."
cat > /usr/local/bin/xray-keepalive << 'EOF'
#!/bin/sh
while true; do
  /usr/local/bin/xray -c /etc/config.json
  echo "[LionXsec] Xray stopped. Restarting in 3 seconds..."
  sleep 3
done
EOF
chmod +x /usr/local/bin/xray-keepalive

echo "[LionXsec] Installed successfully!"
