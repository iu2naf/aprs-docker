#!/usr/bin/env python3

"""
==============================================================================
LoRa iGate APRS-IS Fake Server
==============================================================================

Implementazione di un Fake Server APRS-IS / LoRa iGate multithreaded/multiplexed
basato su socket TCP.

Basato originariamente sullo script cfs.pl di:
  Giovanni - IW1CGW (https://iw1cgw.wordpress.com/2025/06/22/lora-aprs-system)

Modificato e convertito in Python il 28/07/2026 da:
  Diego - IU2NAF
==============================================================================
"""

import hashlib
import os
import select
import signal
import socket
import sys
import time
from datetime import datetime

# ============================================================
# CONFIGURAZIONE (override via variabili d'ambiente)
# ============================================================
PORT_IGATE = int(os.getenv("CFS_PORT_IGATE", "14580"))       # iGate (ricezione nodi LoRa)
PORT_VIEWER = int(os.getenv("CFS_PORT_VIEWER", "14581"))     # visualizzatori / APRStac
HEARTBEAT_INTERVAL = int(os.getenv("CFS_HEARTBEAT_INTERVAL", "30"))
MAX_CLIENTS_PER_PORT = int(os.getenv("CFS_MAX_CLIENTS_PER_PORT", "2"))
IDLE_TIMEOUT = int(os.getenv("CFS_IDLE_TIMEOUT", "300"))     # s di inattivita' prima di disconnettere
NODE_STALE_TIMEOUT = int(os.getenv("CFS_NODE_STALE_TIMEOUT", "600"))  # s senza notizie di un nodo
DEDUP_WINDOW = int(os.getenv("CFS_DEDUP_WINDOW", "60"))      # s della finestra di deduplica
MAX_PACKET_SIZE = int(os.getenv("CFS_MAX_PACKET_SIZE", "1024"))
SEND_TIMEOUT = float(os.getenv("CFS_SEND_TIMEOUT", "2.0"))   # s massimi per un'invio
LOG_DIR = os.getenv("CFS_LOG_DIR", "")
LOG_TO_STDOUT = os.getenv("CFS_LOG_STDOUT", "1") != "0"

PACKET_LOG_FILE = os.path.join(LOG_DIR, "packets.log") if LOG_DIR else "packets.log"
STATUS_LOG_FILE = os.path.join(LOG_DIR, "igate_status.log") if LOG_DIR else "igate_status.log"

# ============================================================
# STATO GLOBALE
# ============================================================
tipo_socket = {}                   # Mappa socket -> porta
client_info = {}                   # Info sui client {sock: {address, connected, callsign, last_activity}}
clients_14580 = []                 # Lista client sulla 14580
clients_14581 = []                 # Lista client sulla 14581
read_buffers = {}                  # Buffer parziali di lettura per ogni client
trascorso = time.time()
packet_counter = 0
last_status_log = time.time()

# Statistiche per nodi e deduplicazione
node_stats = {}                    # {callsign: {last_seen, packets, last_packet}}
recent_packets = {}                # Deduplicazione: {hash contenuto: timestamp}
running = True


# ============================================================
# LOGGING (stdout per `docker logs` + file opzionale)
# ============================================================

def log_line(message):
    line = f"[{get_timestamp()}] {message}"
    if LOG_TO_STDOUT:
        print(line, flush=True)
    if LOG_DIR:
        try:
            with open(STATUS_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ============================================================
# SUBROUTINE DI UTILITY
# ============================================================

def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_packet(direction, data, c_info):
    data_clean = data.strip()
    if LOG_TO_STDOUT:
        print(f"[{get_timestamp()}] {direction} | {c_info} | {data_clean}", flush=True)
    try:
        with open(PACKET_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{get_timestamp()}] {direction} | {c_info} | {data_clean}\n")
    except Exception:
        pass


def log_status(status):
    log_line(status)


def get_clean_callsign(packet):
    # Cerca la stringa prima del carattere '>'
    if '>' in packet:
        call = packet.split('>')[0].strip()
        # Mantiene solo i primi 9 caratteri alfanumerici/trattino
        call_clean = "".join([c for c in call if c.isalnum() or c == '-'])[:9]
        if call_clean:
            return call_clean
    return "UNKNOWN"


def get_packet_hash(packet):
    # Hash del solo contenuto: consente la deduplica vera di pacchetti identici
    norm = "".join(packet.split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def is_duplicate(packet):
    now = time.time()
    packet_hash = get_packet_hash(packet)

    # Pulisce i vecchi pacchetti (oltre la finestra di deduplica)
    expired_keys = [k for k, v in recent_packets.items() if now - v > DEDUP_WINDOW]
    for k in expired_keys:
        del recent_packets[k]

    if packet_hash in recent_packets:
        return True

    recent_packets[packet_hash] = now
    return False


def format_status_message():
    uptime_min = int((time.time() - trascorso) / 60)
    msg = (f"# LoRa iGate Status | Uptime: {uptime_min} min | "
           f"Packets: {packet_counter} | Nodes: {len(node_stats)} | "
           f"Clients: 14580:{len(clients_14580)} 14581:{len(clients_14581)}\r\n")
    return msg


def remove_client(client_sock, inputs_list):
    port = tipo_socket.get(client_sock, -1)
    addr_str = client_info.get(client_sock, {}).get('address', 'Unknown')

    if port == 14580 and client_sock in clients_14580:
        clients_14580.remove(client_sock)
        print(f"RIMOSSO client 14580: {addr_str}", flush=True)
    elif port == 14581 and client_sock in clients_14581:
        clients_14581.remove(client_sock)
        print(f"RIMOSSO client 14581: {addr_str}", flush=True)

    tipo_socket.pop(client_sock, None)
    client_info.pop(client_sock, None)
    read_buffers.pop(client_sock, None)

    if client_sock in inputs_list:
        inputs_list.remove(client_sock)
    try:
        client_sock.close()
    except Exception:
        pass


def send_to_client(client, message, inputs_list):
    # Timeout sull'invio: un client lento viene rimosso invece di bloccare il server
    try:
        client.sendall(message.encode('utf-8'))
    except Exception:
        remove_client(client, inputs_list)


def send_to_all_clients(port, message, inputs_list):
    target_clients = list(clients_14580) if port == 14580 else list(clients_14581)

    for client in target_clients:
        send_to_client(client, message, inputs_list)


def send_login_response(client_sock, callsign):
    port = tipo_socket.get(client_sock, "N/A")
    response = (
        f"# logresp {callsign} verified, server CIVILE-LORAGATE\r\n"
        f"# aprsc 2.1.19-civile-edition\r\n"
        f"# Benvenuto nel sistema LoRa iGate Civile\r\n"
        f"# Nodi attivi: {len(node_stats)}\r\n"
        f"# Pacchetti inoltrati: {packet_counter}\r\n"
    )
    try:
        client_sock.sendall(response.encode('utf-8'))
        print(f"LOGIN OK: {callsign} sulla porta {port}", flush=True)
    except Exception:
        pass


def prune_stale_nodes():
    now = time.time()
    stale = [c for c, st in node_stats.items() if now - st['last_seen'] > NODE_STALE_TIMEOUT]
    for c in stale:
        del node_stats[c]


# ============================================================
# INIZIALIZZAZIONE SERVER
# ============================================================

def create_servers():
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s1.bind(('0.0.0.0', PORT_IGATE))
    s1.listen(MAX_CLIENTS_PER_PORT)

    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s2.bind(('0.0.0.0', PORT_VIEWER))
    s2.listen(MAX_CLIENTS_PER_PORT)

    return s1, s2


# ============================================================
# CICLO PRINCIPALE
# ============================================================

def accept_client(server_sock, port, inputs):
    client, addr = server_sock.accept()
    client_addr = f"{addr[0]}:{addr[1]}"
    target_list = clients_14580 if port == 14580 else clients_14581

    if len(target_list) >= MAX_CLIENTS_PER_PORT:
        print(f"RIFIUTATO: limite raggiunto sulla {port} da {client_addr}", flush=True)
        client.close()
        return

    print(f"NUOVA CONNESSIONE {port} da {client_addr}", flush=True)
    client.settimeout(SEND_TIMEOUT)
    inputs.append(client)
    tipo_socket[client] = port
    client_info[client] = {
        'address': client_addr,
        'connected': time.time(),
        'callsign': "N/A",
        'last_activity': time.time(),
    }
    target_list.append(client)
    log_status(f"Nuovo client {port}: {client_addr}")


def handle_data(sock, inputs):
    try:
        data = sock.recv(MAX_PACKET_SIZE)
    except Exception:
        remove_client(sock, inputs)
        return

    if not data:
        remove_client(sock, inputs)
        return

    client_info[sock]['last_activity'] = time.time()

    # Buffer per riga: evita di troncare o mescolare pacchetti APRS spezzati
    buf = read_buffers.get(sock, b"") + data
    lines = []
    while b"\n" in buf:
        raw, buf = buf.split(b"\n", 1)
        line = raw.decode('utf-8', errors='ignore').strip()
        if line:
            lines.append(line)
    read_buffers[sock] = buf

    for line in lines:
        process_line(sock, line, inputs)


def process_line(sock, line, inputs):
    global packet_counter
    port = tipo_socket.get(sock)
    c_info = client_info.get(sock, {}).get('address', 'unknown')

    # --- GESTIONE LOGIN ---
    if ' pass ' in line:
        parts = line.split()
        callsign = "CIVILE"
        if len(parts) >= 2 and parts[0] == 'user':
            callsign = parts[1]

        client_info[sock]['callsign'] = callsign
        send_login_response(sock, callsign)
        log_status(f"Login: {callsign} sulla porta {port} da {c_info}")
        print(f"LOGIN: {callsign} -> porta {port}", flush=True)

    # --- GESTIONE PACCHETTI APRS (da 14580) ---
    elif port == 14580 and '>' in line:
        print(f"PACCHETTO 14580 da {c_info}: {line}", flush=True)
        callsign = get_clean_callsign(line)

        # Aggiorna statistiche nodo
        if callsign not in node_stats:
            node_stats[callsign] = {}
        node_stats[callsign]['last_seen'] = time.time()
        node_stats[callsign]['packets'] = node_stats[callsign].get('packets', 0) + 1
        node_stats[callsign]['last_packet'] = line

        # Controllo duplicati
        if is_duplicate(line):
            print(f"DUPLICATO IGNORATO: {line}", flush=True)
            return

        packet_counter += 1
        log_packet("RX_14580", line, callsign)

        # Inoltra a TUTTI i client sulla 14581
        if clients_14581:
            out_bytes = (line + "\r\n").encode('utf-8')
            for dest in list(clients_14581):
                try:
                    dest.sendall(out_bytes)
                    print(f"INOLTRATO a 14581: {line}", flush=True)
                except Exception as e:
                    print(f"ERRORE invio a client 14581: {e}", flush=True)
                    remove_client(dest, inputs)
            log_packet("TX_14581", line, callsign)
        else:
            print("NESSUN client 14581 connesso (pacchetto scartato)", flush=True)

    # --- GESTIONE PACCHETTI da 14581 (ack/comandi) ---
    elif port == 14581 and '>' in line:
        print(f"PACCHETTO da 14581 (visualizzatore): {line}", flush=True)
        log_packet("RX_14581", line, "VIEWER")
        print("NOTA: Pacchetto da visualizzatore ignorato (solo ricezione)", flush=True)

    else:
        print(f"MSG da {port}: {line}", flush=True)
        log_packet(f"MSG_{port}", line, c_info)


def main():
    global trascorso, packet_counter, last_status_log

    server14580, server14581 = create_servers()

    print("\n" + "=" * 60)
    print(" LoRa iGate APRS-IS Fake Server (versione civile)")
    print(f" Porta iGate (nodLoRa): {PORT_IGATE}")
    print(f" Porta Visualizzatore: {PORT_VIEWER}")
    print(f" Max client per porta: {MAX_CLIENTS_PER_PORT}")
    print(f" {get_timestamp()}")
    print("=" * 60 + "\n")

    inputs = [server14580, server14581]

    while running:
        now = time.time()

        if now - trascorso >= HEARTBEAT_INTERVAL:
            trascorso = now

            # Prepara messaggio heartbeat
            heartbeat = f"# aprsc 2.1.19-civile {get_timestamp()}\r\n" + format_status_message()

            # Invia heartbeat a tutti i client connessi
            if clients_14580:
                send_to_all_clients(14580, heartbeat, inputs)
            if clients_14581:
                send_to_all_clients(14581, heartbeat, inputs)

            # Pulizia nodi inattivi
            prune_stale_nodes()

            if now - last_status_log > 300:  # Ogni 5 minuti
                log_status(format_status_message())
                last_status_log = now

            print("\n" + "=" * 40)
            print(f"STATO: {get_timestamp()}")
            print(f"Client 14580: {len(clients_14580)}")
            print(f"Client 14581: {len(clients_14581)}")
            print(f"Nodi LoRa attivi: {len(node_stats)}")
            print(f"Pacchetti totali: {packet_counter}")
            print("=" * 40)

        # Rimuove i client inattivi da troppo tempo
        idle_cutoff = now - IDLE_TIMEOUT
        for sock in list(client_info):
            if client_info[sock]['last_activity'] < idle_cutoff:
                print(f"IDLE TIMEOUT: disconnesso {client_info[sock]['address']}", flush=True)
                remove_client(sock, inputs)

        readable, _, _ = select.select(inputs, [], [], 0.5)

        for sock in readable:
            if sock is server14580:
                accept_client(server14580, 14580, inputs)
            elif sock is server14581:
                accept_client(server14581, 14581, inputs)
            else:
                handle_data(sock, inputs)

    print("\nArresto del server...", flush=True)
    for sock in list(client_info):
        remove_client(sock, inputs)
    server14580.close()
    server14581.close()


def _handle_signal(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArresto del server...", flush=True)
