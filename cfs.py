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

import socket
import select
import time
from datetime import datetime

# ============================================================
# CONFIGURAZIONE
# ============================================================
PORT_IGATE = 14580           # Porta per l'iGate (ricezione nodi LoRa)
PORT_VIEWER = 14581          # Porta per APRStac e visualizzatori
HEARTBEAT_INTERVAL = 30      # Intervallo heartbeat (secondi)
MAX_CLIENTS_PER_PORT = 2     # Max client per porta
PACKET_LOG_FILE = "packets.log"
STATUS_LOG_FILE = "igate_status.log"
MAX_PACKET_SIZE = 1024       # Dimensione massima pacchetto

# ============================================================
# STATO GLOBALE
# ============================================================
tipo_socket = {}                   # Mappa socket -> porta
client_info = {}                   # Info sui client {sock: {address, connected, callsign}}
clients_14580 = []                 # Lista client sulla 14580
clients_14581 = []                 # Lista client sulla 14581
trascorso = time.time()
packet_counter = 0
last_status_log = time.time()

# Statistiche per nodi e deduplicazione
node_stats = {}                    # Statistiche per ogni nodo (SSID): {callsign: {last_seen, packets, last_packet}}
recent_packets = {}                # Deduplicazione pacchetti: {hash: timestamp}

# ============================================================
# SUBROUTINE DI UTILITY
# ============================================================

def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_packet(direction, data, c_info):
    data_clean = data.strip()
    try:
        with open(PACKET_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{get_timestamp()}] {direction} | {c_info} | {data_clean}\n")
    except Exception:
        pass

def log_status(status):
    try:
        with open(STATUS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{get_timestamp()}] {status}\n")
    except Exception:
        pass

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
    # Rimuove spazi vuoti e a capo
    hash_str = "".join(packet.split())
    # Prende solo i primi 50 caratteri + timestamp corrente
    return hash_str[:50] + str(int(time.time()))

def is_duplicate(packet):
    now = time.time()
    packet_hash = get_packet_hash(packet)
    
    # Pulisce i vecchi pacchetti (oltre 60 secondi)
    expired_keys = [k for k, v in recent_packets.items() if now - v > 60]
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
        print(f"RIMOSSO client 14580: {addr_str}")
    elif port == 14581 and client_sock in clients_14581:
        clients_14581.remove(client_sock)
        print(f"RIMOSSO client 14581: {addr_str}")
        
    tipo_socket.pop(client_sock, None)
    client_info.pop(client_sock, None)
    
    if client_sock in inputs_list:
        inputs_list.remove(client_sock)
    try:
        client_sock.close()
    except Exception:
        pass

def send_to_all_clients(port, message, inputs_list):
    target_clients = list(clients_14580) if port == 14580 else list(clients_14581)
    
    for client in target_clients:
        try:
            client.sendall(message.encode('utf-8'))
        except Exception:
            remove_client(client, inputs_list)

def send_login_response(client_sock, callsign):
    port = tipo_socket.get(client_sock, "N/A")
    response = (
        f"# logresp {callsign} verified, server CIVILE-LORAGATE, (c) 2026\r\n"
        f"# aprsc 2.1.19-civile-edition\r\n"
        f"# Benvenuto nel sistema LoRa iGate Civile\r\n"
        f"# Nodi attivi: {len(node_stats)}\r\n"
        f"# Pacchetti inoltrati: {packet_counter}\r\n"
    )
    try:
        client_sock.sendall(response.encode('utf-8'))
        print(f"LOGIN OK: {callsign} sulla porta {port}")
    except Exception:
        pass

# ============================================================
# INIZIALIZZAZIONE SERVER
# ============================================================
server14580 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server14580.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server14580.bind(('0.0.0.0', PORT_IGATE))
server14580.listen(MAX_CLIENTS_PER_PORT)

server14581 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server14581.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server14581.bind(('0.0.0.0', PORT_VIEWER))
server14581.listen(MAX_CLIENTS_PER_PORT)

print("\n" + "=" * 60)
print(" LoRa iGate APRS-IS Fake Server (versione civile)")
print(f" Porta iGate (nodLoRa): {PORT_IGATE}")
print(f" Porta Visualizzatore: {PORT_VIEWER}")
print(f" Max client per porta: {MAX_CLIENTS_PER_PORT}")
print(f" {get_timestamp()}")
print("=" * 60 + "\n")

inputs = [server14580, server14581]

# ============================================================
# CICLO PRINCIPALE
# ============================================================
def main():
    global trascorso, packet_counter, last_status_log
    
    while True:
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

        readable, _, _ = select.select(inputs, [], [], 0.5)
        
        for sock in readable:
            # --- NUOVA CONNESSIONE 14580 ---
            if sock is server14580:
                client, addr = server14580.accept()
                client_addr = f"{addr[0]}:{addr[1]}"
                
                if len(clients_14580) >= MAX_CLIENTS_PER_PORT:
                    print(f"RIFIUTATO: limite raggiunto sulla 14580 da {client_addr}")
                    client.close()
                    continue
                    
                print(f"NUOVA CONNESSIONE 14580 da {client_addr}")
                inputs.append(client)
                tipo_socket[client] = 14580
                client_info[client] = {
                    'address': client_addr,
                    'connected': time.time(),
                    'callsign': "N/A"
                }
                clients_14580.append(client)
                log_status(f"Nuovo client 14580: {client_addr}")

            # --- NUOVA CONNESSIONE 14581 ---
            elif sock is server14581:
                client, addr = server14581.accept()
                client_addr = f"{addr[0]}:{addr[1]}"
                
                if len(clients_14581) >= MAX_CLIENTS_PER_PORT:
                    print(f"RIFIUTATO: limite raggiunto sulla 14581 da {client_addr}")
                    client.close()
                    continue
                    
                print(f"NUOVA CONNESSIONE 14581 da {client_addr}")
                inputs.append(client)
                tipo_socket[client] = 14581
                client_info[client] = {
                    'address': client_addr,
                    'connected': time.time(),
                    'callsign': "N/A"
                }
                clients_14581.append(client)
                log_status(f"Nuovo client 14581: {client_addr}")

            # --- GESTIONE DATI ---
            else:
                try:
                    data = sock.recv(MAX_PACKET_SIZE)
                except Exception:
                    data = None

                if not data:
                    remove_client(sock, inputs)
                    continue
                
                line = data.decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                
                port = tipo_socket.get(sock)
                c_info = client_info.get(sock, {}).get('address', 'unknown')
                
                # --- GESTIONE LOGIN ---
                if ' pass ' in line:
                    # Estrae callsign
                    parts = line.split()
                    callsign = "CIVILE"
                    if len(parts) >= 2 and parts[0] == 'user':
                        callsign = parts[1]
                        
                    client_info[sock]['callsign'] = callsign
                    send_login_response(sock, callsign)
                    log_status(f"Login: {callsign} sulla porta {port} da {c_info}")
                    print(f"LOGIN: {callsign} -> porta {port}")

                # --- GESTIONE PACCHETTI APRS (da 14580) ---
                elif port == 14580 and '>' in line:
                    print(f"PACCHETTO 14580 da {c_info}: {line}")
                    callsign = get_clean_callsign(line)
                    
                    # Aggiorna statistiche nodo
                    if callsign not in node_stats:
                        node_stats[callsign] = {}
                    node_stats[callsign]['last_seen'] = time.time()
                    node_stats[callsign]['packets'] = node_stats[callsign].get('packets', 0) + 1
                    node_stats[callsign]['last_packet'] = line
                    
                    # Controllo duplicati
                    if is_duplicate(line):
                        print(f"DUPLICATO IGNORATO: {line}")
                        continue
                        
                    packet_counter += 1
                    log_packet("RX_14580", line, callsign)
                    
                    # Inoltra a TUTTI i client sulla 14581
                    if clients_14581:
                        out_bytes = (line + "\r\n").encode('utf-8')
                        for dest in list(clients_14581):
                            try:
                                dest.sendall(out_bytes)
                                print(f"INOLTRATO a 14581: {line}")
                            except Exception as e:
                                print(f"ERRORE invio a client 14581: {e}")
                                remove_client(dest, inputs)
                        log_packet("TX_14581", line, callsign)
                    else:
                        print("NESSUN client 14581 connesso (pacchetto scartato)")

                # --- GESTIONE PACCHETTI da 14581 (ack/comandi) ---
                elif port == 14581 and '>' in line:
                    print(f"PACCHETTO da 14581 (visualizzatore): {line}")
                    log_packet("RX_14581", line, "VIEWER")
                    print("NOTA: Pacchetto da visualizzatore ignorato (solo ricezione)")

                else:
                    print(f"MSG da {port}: {line}")
                    log_packet(f"MSG_{port}", line, c_info)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArresto del server...")
    finally:
        server14580.close()
        server14581.close()
