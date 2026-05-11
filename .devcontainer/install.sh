#!/bin/sh

echo "[LionXsec] Downloading Xray Core..."
wget -O ${PWD}/xray.zip https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip

echo "[LionXsec] Installing..."
unzip -o xray.zip
chmod +x xray
mv xray /usr/local/bin/xray

UUID=$(cat /proc/sys/kernel/random/uuid)
sed -i "s/550e8400-e29b-41d4-a716-446655440000/$UUID/g" /workspaces/*/.devcontainer/config.json

cat > /usr/local/bin/xray-keepalive << 'EOF'
#!/bin/sh
while true
do
  /usr/local/bin/xray -c /etc/config.json
  echo "[LionXsec] Xray crashed. Restarting in 3 seconds..."
  sleep 3
done
EOF

chmod +x /usr/local/bin/xray-keepalive

echo "[LionXsec] Installed successfully!"
