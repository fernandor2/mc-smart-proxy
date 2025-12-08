import time
import socket
import requests
import subprocess
import json
import os
import threading
import re
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CRAFTY_URL = os.getenv("CRAFTY_URL", "http://192.168.1.10:8443")
TOKEN = os.getenv("CRAFTY_TOKEN", "YOUR_API_TOKEN")
SERVER_ID = os.getenv("SERVER_ID", "1")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "25565"))
REAL_SERVER_PORT = int(os.getenv("REAL_SERVER_PORT", "25599"))
REAL_SERVER_IP = os.getenv("REAL_SERVER_IP", "127.0.0.1")
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "10"))*60

BOOT_CACHE_FILE = "boot_time.txt"
startup_estimate = 120

def load_startup_time():
    global startup_estimate
    if os.path.exists(BOOT_CACHE_FILE):
        try:
            with open(BOOT_CACHE_FILE, "r") as f:
                val = int(float(f.read().strip()))
                startup_estimate = max(10, val)
                print(f"Loaded stored boot time: {startup_estimate}s")
        except Exception:
            pass

def save_startup_time(seconds):
    global startup_estimate
    try:
        startup_estimate = int(seconds)
        with open(BOOT_CACHE_FILE, "w") as f:
            f.write(str(startup_estimate))
        print(f"✅ Boot time learned and saved: {startup_estimate}s")
    except Exception as e:
        print(f"Failed to save boot time: {e}")

load_startup_time()

MOTD_SLEEPING = "§6💤 Server is Sleeping\n§fJoin to wake it up!"
MOTD_WAKING = "§e⚙️ Server is starting...\n§fPlease wait ~20 seconds."

last_active_time = time.time()
proxy_process = None
is_waking = False
wake_start_time = 0
lock = threading.RLock()

PROTOCOL_DB_URL = "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/pc/common/protocolVersions.json"

def get_protocol_map():
    try:
        resp = requests.get(PROTOCOL_DB_URL, timeout=2)
        if resp.status_code == 200:
            return {entry["minecraftVersion"]: entry["version"] for entry in resp.json()}
    except Exception:
        print("⚠️ Could not download protocol list. Using fallback.")
    return {}

def get_real_server_info():
    detected_version = "1.21"
    protocol = 767

    try:
        url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}"
        resp = requests.get(url, headers=get_headers(), verify=False, timeout=5)

        if resp.status_code == 200:
            data = resp.json().get("data", {})
            crafty_filename = data.get("execution_command") or data.get("executable") or ""

            match = re.search(r"(\d+\.\d+(\.\d+)?)", crafty_filename)
            if match:
                detected_version = match.group(1)
                protocol_map = get_protocol_map()
                protocol = protocol_map.get(detected_version, 767)
            else:
                print(f"⚠️ Warning: No version number found in command '{crafty_filename}'")
        else:
            print(f"⚠️ API Error getting server info: {resp.status_code}")

    except Exception as e:
        print(f"⚠️ Error in get_real_server_info: {e}")

    return detected_version, protocol

def pack_varint(value: int) -> bytes:
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
        else:
            print(f"Error API Code: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        print(f"Error getting server status: {e}")
        return None

def send_start_request_worker():
    global is_waking, wake_start_time
    success = False
    try:
        url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/start_server"
        resp = requests.post(
            url,
            headers=get_headers(),
            verify=False,
            timeout=5,
        )
        if resp.status_code != 200:
            print(f"❌ START FAILED: Code {resp.status_code}")
            print(f"Crafty Response: {resp.text}")
        else:
            print("✅ Start command sent successfully to Crafty.")
            success = True
            
    except Exception as e:
        print(f"Failed to start: {e}")
    
    if not success:
        with lock:
            print("⚠️ Start request failed. Resetting waking state.")
            is_waking = False
            wake_start_time = 0

def start_server():
    global is_waking, wake_start_time
    should_start = False
    
    with lock:
        if not is_waking:
            print("Wake signal received! Starting server...")
            is_waking = True
            wake_start_time = time.time()
            should_start = True

    if should_start:
        threading.Thread(target=send_start_request_worker, daemon=True).start()

def stop_server():
    print("Idle timeout reached. Stopping server...")
    try:
        requests.post(
            f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/stop_server",
            headers=get_headers(),
            verify=False,
            timeout=5,
        )
    except Exception as e:
        print(f"Failed to stop: {e}")

def is_port_open(ip, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            return s.connect_ex((ip, port)) == 0
    except:
        return False

def start_proxy():
    global proxy_process, is_waking
    with lock:
        if proxy_process is not None and proxy_process.poll() is not None:
            print("⚠️ Proxy process died surprisingly. Clearing state.")
            proxy_process = None
        if proxy_process is None:
            print(f"Server is UP. Starting socat proxy to {REAL_SERVER_IP}:{REAL_SERVER_PORT}...")
            cmd = [
                "socat",
                f"TCP-LISTEN:{LISTEN_PORT},fork,reuseaddr,tcp-nodelay",
                f"TCP:{REAL_SERVER_IP}:{REAL_SERVER_PORT},tcp-nodelay",
            ]
            proxy_process = subprocess.Popen(cmd)
            is_waking = False

def stop_proxy():
    global proxy_process
    with lock:
        if proxy_process:
            print("Stopping proxy...")
            proxy_process.terminate()
            try:
                proxy_process.wait(timeout=2)
            except:
                proxy_process.kill()
            proxy_process = None

def read_bytes(sock, length):
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise Exception("Connection closed during read")
        data += chunk
    return data

def read_varint(sock):
    data = 0
    for i in range(5):
        b = sock.recv(1)
        if not b:
            raise Exception("Connection closed")
        byte = b[0]
        data |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return data
    return data

def send_packet(sock, data):
    sock.sendall(pack_varint(len(data)) + data)

def handle_client(conn):
    global is_waking, wake_start_time, startup_estimate, last_active_time
    try:
        conn.settimeout(5.0)
        print(f"Connection received from {conn.getpeername()}")
        
        packet_len = read_varint(conn)
        packet_id = read_varint(conn)
        
        if packet_id == 0x00:
            proto_ver = read_varint(conn)
            addr_len = read_varint(conn)
            read_bytes(conn, addr_len)
            read_bytes(conn, 2)
            next_state = read_varint(conn)

            if next_state == 1:
                with lock:
                    last_active_time = time.time()
                
                with lock:
                    if is_waking:
                        elapsed = time.time() - wake_start_time if wake_start_time > 0 else 0
                        remaining = max(0, startup_estimate - int(elapsed))
                        
                        if remaining > 0:
                            mins, secs = divmod(remaining, 60)
                            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                            motd_text = f"§e⚙️ Starting... (§6{time_str} left§e)\n§fRefining estimate..."
                        else:
                            motd_text = "§e⚙️ Starting... (§6Almost done...§e)\n§fFinalizing load..."
                    else:
                        motd_text = MOTD_SLEEPING

                mc_version, protocol = get_real_server_info()
                status = {
                    "version": {"name": mc_version, "protocol": protocol},
                    "players": {"max": 20, "online": 0, "sample": []},
                    "description": {"text": motd_text},
                }
                resp_payload = b"\x00" + pack_string(json.dumps(status))
                send_packet(conn, resp_payload)

            elif next_state == 2:
                with lock:
                    last_active_time = time.time()
                start_server()

                with lock:
                    if is_waking:
                        elapsed = time.time() - wake_start_time if wake_start_time > 0 else 0
                        remaining = max(0, startup_estimate - int(elapsed))
                        
                        if remaining > 0:
                            mins, secs = divmod(remaining, 60)
                            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                            kick_text = (
                                f"§6⚙️ Server is starting...\n\n"
                                f"§fEst. remaining: §e{time_str}\n"
                                f"§7We are learning your server speed!"
                            )
                        else:
                            kick_text = (
                                f"§6⚙️ Server is starting...\n\n"
                                f"§fStatus: §eFinalizing load...\n"
                                f"§7Please wait a moment."
                            )
                    else:
                        kick_text = "§bWake signal sent!\n§7Server will be ready soon."

                msg = {"text": kick_text}
                kick_payload = b"\x00" + pack_string(json.dumps(msg))
                send_packet(conn, kick_payload)
            else:
                print(f"Unknown next_state: {next_state}")

        else:
            print(f"Ignored packet ID: {packet_id} (Expected 0x00 Handshake)")

    except Exception as e:
        print(f"Handshake error: {e}") 
        pass
    finally:
        try:
            conn.shutdown(socket.SHUT_WR)
            conn.settimeout(2.0)
            while conn.recv(1024):
                pass
        except Exception:
            pass
        conn.close()

def is_server_fully_ready(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=2) as sock:
            protocol_ver = pack_varint(767) 
            addr = pack_string(ip)
            port_bytes = port.to_bytes(2, byteorder='big')
            next_state = pack_varint(1)
            
            handshake_data = protocol_ver + addr + port_bytes + next_state
            send_packet(sock, b"\x00" + handshake_data)
            
            send_packet(sock, b"\x00")
            
            _ = read_varint(sock)
            packet_id = read_varint(sock)
            
            if packet_id == 0x00:
                json_len = read_varint(sock)
                if json_len > 0:
                    return True
    except Exception:
        pass
    
    return False

def check_readiness_worker(stop_event, ready_event):
    while not stop_event.is_set():
        try:
            status = get_server_status()
            should_check_port = False

            if status is None:
                should_check_port = True
            else:
                running, _ = status
                if running:
                    should_check_port = True

            if should_check_port:
                if is_server_fully_ready(REAL_SERVER_IP, REAL_SERVER_PORT):
                    ready_event.set()
                    break
        except Exception:
            pass
        
        time.sleep(1)

def run_fake_server():
    global is_waking, wake_start_time
    
    stop_check = threading.Event()
    server_ready_evt = threading.Event()
    is_timeout = False
    
    t = threading.Thread(target=check_readiness_worker, args=(stop_check, server_ready_evt))
    t.start()

    print(">> Fake Server Listening (Waiting for Real Server)...")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', LISTEN_PORT))
            s.listen(5)
            s.settimeout(1.0)
            
            while not server_ready_evt.is_set():
                with lock:
                    checking_waking = is_waking
                    checking_start_time = wake_start_time

                if checking_waking and checking_start_time > 0:
                    if (time.time() - checking_start_time) > 300:
                        print("⚠️ Server stuck in 'Starting' phase for too long. Aborting to force stop.")
                        with lock:
                            is_waking = False
                            wake_start_time = 0
                        
                        is_timeout = True
                        break

                try:
                    conn, addr = s.accept()
                    client_t = threading.Thread(target=handle_client, args=(conn,))
                    client_t.start()
                except socket.timeout:
                    continue 
                except Exception as e:
                    print(f"Socket error: {e}")
                    time.sleep(1)
    except Exception as e:
        print(f"Error binding fake server: {e}")
        time.sleep(2)
    finally:
        stop_check.set()
        t.join()
        if not is_timeout:
            print(">> Real Server Ready! Switch to Proxy.")
            
    return is_timeout

def main():
    global last_active_time, is_waking, wake_start_time
    print("--- Smart Manager Started (Auto-Learning) ---")
    
    was_ready = False
    failed_checks = 0

    while True:
        status = get_server_status()
        if status is None:
            print("⚠️ API Unreachable. Checking ping for fallback status...")
            if is_server_fully_ready(REAL_SERVER_IP, REAL_SERVER_PORT):
                running = True
                players = 0
                print("   -> Ping OK. Assuming Server ON (Players=0).")
            else:
                running = False
                players = 0
                print("   -> Ping Failed. Assuming Server OFF.")
        else:
            running, players = status
        
        server_ready = False
        if running:
            is_ready_now = is_server_fully_ready(REAL_SERVER_IP, REAL_SERVER_PORT)
            
            if is_ready_now:
                server_ready = True
                failed_checks = 0
            else:
                with lock:
                    is_proxy_running = proxy_process is not None
                
                if is_proxy_running:
                    failed_checks += 1
                    if failed_checks < 3:
                        print(f"⚠️ Server ping failed ({failed_checks}/3). Ignoring temporarily...")
                        server_ready = True
                    else:
                        print("❌ Server confirmed dead after 3 failures.")
                        server_ready = False
                else:
                    server_ready = False        
        if server_ready:
            if not was_ready:
                print("✨ Server detected as JUST READY. Resetting idle timer.")
                with lock:
                    last_active_time = time.time()
                failed_checks = 0

            with lock:
                waking_status = is_waking

            if waking_status:
                print("Server detected as ONLINE!")
                with lock:
                    if wake_start_time > 0:
                        actual_duration = time.time() - wake_start_time
                        print(f"Server took {int(actual_duration)}s to start.")
                        save_startup_time(actual_duration)
                        wake_start_time = 0

                    is_waking = False
                    last_active_time = time.time()
            
            start_proxy()
            
            should_stop = False
            with lock:
                if players > 0:
                    last_active_time = time.time()
                elif (time.time() - last_active_time) > IDLE_TIMEOUT:
                    should_stop = True
            
            if should_stop:
                stop_server()
                stop_proxy()
                time.sleep(10)
            
            time.sleep(5) 
            
        else:
            stop_proxy()
            timed_out_in_fake = run_fake_server()
            
            if timed_out_in_fake:
                stop_server()
            
            with lock:
                if is_waking:
                    pass

        was_ready = server_ready

if __name__ == "__main__":
    main()