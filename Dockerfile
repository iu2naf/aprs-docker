FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    ca-certificates \
    curl \
    sqlite3 \
    procps \
    vim curl htop mc net-tools \
    && rm -rf /var/lib/apt/lists/*

ARG APSTAC_DEB_URL=https://aprstac.com/downloads/aprstac_0.2.4_amd64.deb
RUN curl -fsSL "$APSTAC_DEB_URL" -o /tmp/aprstac.deb \
    && dpkg -i /tmp/aprstac.deb \
    && rm -f /tmp/aprstac.deb

WORKDIR /opt/aprstac

COPY cfs.py /usr/local/bin/cfs.py
RUN chmod +x /usr/local/bin/cfs.py

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 14501 14580 14581

ENTRYPOINT ["/entrypoint.sh"]
