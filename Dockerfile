FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    ca-certificates \
    curl \
    sqlite3 \
    procps \
    && rm -rf /var/lib/apt/lists/*

ARG APSTAC_DEB_URL=https://aprstac.com/downloads/aprstac_0.2.4_amd64.deb
ARG APSTAC_DEB_SHA256=d05ef7799bfff389628347e22d0a740675f23532760abd08ec00c4935dce24f3
RUN curl -fsSL "$APSTAC_DEB_URL" -o /tmp/aprstac.deb \
    && echo "$APSTAC_DEB_SHA256  /tmp/aprstac.deb" | sha256sum -c - \
    && dpkg -i /tmp/aprstac.deb \
    && rm -f /tmp/aprstac.deb

WORKDIR /opt/aprstac

COPY cfs.py /usr/local/bin/cfs.py
RUN chmod +x /usr/local/bin/cfs.py

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Esegue come utente non privilegiato
RUN useradd -m -u 1001 aprstac \
    && mkdir -p /opt/aprstac/data /opt/aprstac/logs \
    && chown -R aprstac:aprstac /opt/aprstac
USER aprstac

EXPOSE 14501 14580 14581

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import socket; s=socket.create_connection(('127.0.0.1', 14581), 3); s.close()" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
