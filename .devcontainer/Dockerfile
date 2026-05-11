FROM debian:bookworm-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash git curl wget unzip tzdata openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY install.sh /app/install.sh
RUN chmod +x /app/install.sh && /app/install.sh

COPY config.json /etc/config.json

# Generate a random UUID and replace placeholder in config
RUN UUID=$(cat /proc/sys/kernel/random/uuid) && \
    sed -i "s/XRAY_UUID_PLACEHOLDER/$UUID/g" /etc/config.json && \
    echo "UUID=$UUID" > /etc/xray-uuid.env

# Add link display to bash startup
RUN echo '#!/bin/sh\n\
UUID=$(grep "id" /etc/config.json | head -1 | tr -d " \",id:")\n\
echo ""\n\
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"\n\
echo "✅  LionXsec Tunnel - Ready to Connect"\n\
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"\n\
echo ""\n\
echo "🔗 VLESS LINK:"\n\
echo "vless://${UUID}@${CODESPACE_NAME}-443.app.github.dev:443?encryption=none&security=tls&type=xhttp&mode=packet-up&sni=${CODESPACE_NAME}-443.app.github.dev&path=%2F#LionXsec"\n\
echo ""\n\
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"\n\
' > /usr/local/bin/show-link && chmod +x /usr/local/bin/show-link

RUN echo '/usr/local/bin/show-link' >> /etc/bash.bashrc

CMD ["/usr/local/bin/xray-keepalive"]
