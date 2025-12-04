import time
import socket
import requests
import subprocess
import json
import os
import threading

# --- CONFIGURATION ---
CRAFTY_URL = os.getenv("CRAFTY_URL", "http://192.168.1.10:8443")
TOKEN = os.getenv("CRAFTY_TOKEN", "YOUR_API_TOKEN")
SERVER_ID = os.getenv("SERVER_ID", "1")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "25565"))
REAL_SERVER_PORT = int(os.getenv("REAL_SERVER_PORT", "25599"))
REAL_SERVER_IP = os.getenv("REAL_SERVER_IP", "127.0.0.1")
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "600"))

# MOTD Settings
MOTD_SLEEPING = "§6💤 Server is Sleeping\n§fJoin to wake it up!"
MOTD_WAKING = "§e⚙️ Server is starting...\n§fPlease wait ~20 seconds."

# Global State
last_active_time = time.time()
proxy_process = None
is_waking = False


def pack_varint(value: int) -> bytes:
    """Encode an integer as a Minecraft VarInt."""
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        out += bytes([byte])
        if not value:
            break
    return out


def pack_string(s: str) -> bytes:
    """Encode a string with VarInt length prefix."""
    data = s.encode("utf-8")
    return pack_varint(len(data)) + data


def get_headers():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def get_server_status():
    try:
        url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/stats"
        resp = requests.get(url, headers=get_headers(), verify=False, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return data.get("running", False), data.get("online", 0)
    except Exception as e:
        print(f"Error getting server status: {e}")
    return False, 0


def start_server():
    global is_waking
    if is_waking:
        return
    print("Wake signal received! Starting server...")
    is_waking = True
    try:
        requests.post(
            f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/start",
            headers=get_headers(),
            verify=False,
            timeout=5,
        )
    except Exception as e:
        print(f"Failed to start: {e}")


def stop_server():
    print("Idle timeout reached. Stopping server...")
    try:
        requests.post(
            f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/stop",
            headers=get_headers(),
            verify=False,
            timeout=5,
        )
    except Exception as e:
        print(f"Failed to stop: {e}")


def start_proxy():
    global proxy_process, is_waking
    if proxy_process is None:
        print("Server is UP. Starting socat proxy...")
        cmd = [
            "socat",
            f"TCP-LISTEN:{LISTEN_PORT},fork,reuseaddr",
            f"TCP:{REAL_SERVER_IP}:{REAL_SERVER_PORT}",
        ]
        proxy_process = subprocess.Popen(cmd)
        is_waking = False  # Reset waking state


def stop_proxy():
    global proxy_process
    if proxy_process:
        print("Stopping proxy...")
        proxy_process.terminate()
        proxy_process = None


# --- MINECRAFT PROTOCOL HANDLERS ---
def read_varint(sock):
    data = 0
    for i in range(5):
        b = sock.recv(1)
        if not b:
            raise Exception("Connection closed")
        # b is a single byte (bytes object of length 1)
        byte = b[0]
        data |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return data
    return data


def send_packet(sock, data):
    """Send a Minecraft packet with VarInt length prefix."""
    sock.sendall(pack_varint(len(data)) + data)

def handle_client(conn):
    global is_waking
    try:
        # 1. Read Handshake Packet
        packet_len = read_varint(conn)
        packet_id = read_varint(conn)  # 0x00 is Handshake
        
        if packet_id == 0x00:
            # Read Protocol Version (VarInt)
            proto_ver = read_varint(conn)
            # Read Server Address (String)
            addr_len = read_varint(conn)
            conn.recv(addr_len) 
            # Read Port (Unsigned Short)
            conn.recv(2)
            # Read Next State (VarInt): 1=Status, 2=Login
            next_state = read_varint(conn)

            if next_state == 1:  # STATUS PING (Server List)
                # Send Status Response
                status = {
                    "version": {"name": "1.21", "protocol": 767},
                    "players": {"max": 20, "online": 0, "sample": []},
                    "description": {"text": MOTD_WAKING if is_waking else MOTD_SLEEPING}
                }
                # Packet ID 0x00 (Response) + JSON String
                json_data = json.dumps(status).encode('utf-8')
                # VarInt(0) + VarInt(len) + String
                data = b'\x00' + getattr(len(json_data).to_bytes(5, 'big'), 'lstrip')(b'\x00') # Simple VarInt hack for len
                # Actually, let's just use a simple packer for safety
                def pack_string(s):
                    b = s.encode('utf-8')
                    l = len(b)
                    # Simple VarInt for length
                    out = b''
                    while True:
                        byte = l & 0x7F
                        l >>= 7
                        if l: byte |= 0x80
                        out += bytes([byte])
                        if not l: break
                    return out + b
                
                resp_payload = b"\x00" + pack_string(json.dumps(status))
                send_packet(conn, resp_payload)

            elif next_state == 2:  # LOGIN ATTEMPT (Joining)
                # Player is trying to join! Wake the server.
                start_server()
                # Send a "Kick" packet with a message explanation
                msg = {"text": "§bWake signal sent!\n§7Wait ~20s and refresh."}
                kick_payload = b"\x00" + pack_string(json.dumps(msg))
                send_packet(conn, kick_payload)

    except Exception as e:
        print(f"Error handling client: {e}")
    finally:
        conn.close()

def run_fake_server():
    """Listens on 25565 and pretends to be Minecraft."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', LISTEN_PORT))
        s.listen(5)
        s.settimeout(2)
        print(f"Fake Server listening on {LISTEN_PORT}...")
        
        while proxy_process is None: # Only run if proxy is NOT running
            try:
                conn, addr = s.accept()
                # Handle each ping in a thread so we don't block
                t = threading.Thread(target=handle_client, args=(conn,))
                t.start()
            except socket.timeout:
                continue
            except:
                break

# --- MAIN LOOP ---
def main():
    global last_active_time, is_waking
    print("--- Smart Manager Started ---")
    
    while True:
        running, players = get_server_status()
        
        if running:
            # Server is ONLINE
            start_proxy() # Ensure socat is piping traffic
            is_waking = False
            
            if players > 0:
                last_active_time = time.time()
            elif (time.time() - last_active_time) > IDLE_TIMEOUT:
                stop_server()
                stop_proxy()
                time.sleep(5) # Give it a moment to die
        else:
            # Server is OFFLINE
            stop_proxy() # Ensure socat is dead
            # Run the fake listener loop for one cycle (it exits if proxy starts)
            run_fake_server()
            
            # If we broke out of run_fake_server, check if we need to wait for boot
            if is_waking:
                print("Waiting for server boot...")
                time.sleep(5)

if __name__ == "__main__":
    main()