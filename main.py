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

# --- CONFIGURATION ---
CRAFTY_URL = os.getenv("CRAFTY_URL", "http://192.168.1.10:8443")
TOKEN = os.getenv("CRAFTY_TOKEN", "YOUR_API_TOKEN")
SERVER_ID = os.getenv("SERVER_ID", "1")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "25565"))
REAL_SERVER_PORT = int(os.getenv("REAL_SERVER_PORT", "25599"))
REAL_SERVER_IP = os.getenv("REAL_SERVER_IP", "127.0.0.1")
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "600"))*60

# --- AUTO-LEARNING SYSTEM ---
BOOT_CACHE_FILE = "boot_time.txt"

# Tiempo por defecto para la PRIMERA vez (ej: 60s)
startup_estimate = 60


def load_startup_time():
    """Lee el tiempo de arranque estimado desde disco (si existe)."""
    global startup_estimate
    if os.path.exists(BOOT_CACHE_FILE):
        try:
            with open(BOOT_CACHE_FILE, "r") as f:
                val = int(float(f.read().strip()))
                # Mínimo de seguridad para no romper nada
                startup_estimate = max(10, val)
                print(f"Loaded stored boot time: {startup_estimate}s")
        except Exception:
            pass


def save_startup_time(seconds):
    """Guarda el nuevo tiempo de arranque aprendido."""
    global startup_estimate
    try:
        startup_estimate = int(seconds)
        with open(BOOT_CACHE_FILE, "w") as f:
            f.write(str(startup_estimate))
        print(f"✅ Boot time learned and saved: {startup_estimate}s")
    except Exception as e:
        print(f"Failed to save boot time: {e}")


# Cargamos el dato al iniciar el script
load_startup_time()

# MOTD Settings
MOTD_SLEEPING = "§6💤 Server is Sleeping\n§fJoin to wake it up!"
MOTD_WAKING = "§e⚙️ Server is starting...\n§fPlease wait ~20 seconds."

# Global State
last_active_time = time.time()
proxy_process = None
is_waking = False
wake_start_time = 0
lock = threading.RLock()


# URL mantenida por la comunidad con todas las versiones
PROTOCOL_DB_URL = "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/pc/common/protocolVersions.json"


def get_protocol_map():
    """Descarga la lista de versiones actualizadas de internet."""
    try:
        resp = requests.get(PROTOCOL_DB_URL, timeout=2)
        if resp.status_code == 200:
            # Crea un diccionario simple: {'1.20.1': 763, '1.21': 767, ...}
            return {entry["minecraftVersion"]: entry["version"] for entry in resp.json()}
    except Exception:
        print("⚠️ No se pudo descargar la lista de protocolos. Usando fallback.")
    return {}


def get_real_server_info():
    """
    Obtiene versión y protocolo del servidor real consultando la API de Crafty.
    """
    # Valores por defecto (Fallback)
    version_detectada = "1.21"
    protocolo = 767

    try:
        # 1. Obtenemos los detalles del servidor desde la API
        url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}"
        resp = requests.get(url, headers=get_headers(), verify=False, timeout=5)

        if resp.status_code == 200:
            data = resp.json().get("data", {})
            
            # Buscamos el comando de ejecución o el nombre del ejecutable
            # Crafty devuelve 'execution_command' con el comando completo (ej: java -jar server-1.20.4.jar)
            crafty_filename = data.get("execution_command") or data.get("executable") or ""

            # 2. Extraemos la versión del texto (ej: "1.20.4")
            match = re.search(r"(\d+\.\d+(\.\d+)?)", crafty_filename)
            if match:
                version_detectada = match.group(1)

                # 3. Buscamos el protocolo en nuestra lista descargada
                protocol_map = get_protocol_map()
                protocolo = protocol_map.get(version_detectada, 767) # 767 = 1.21 default
                
                # Opcional: Imprimir para debug
                # print(f"Detected version: {version_detectada} (Proto: {protocolo})")
            else:
                print(f"⚠️ Warning: No version number found in command '{crafty_filename}'")
        else:
            print(f"⚠️ API Error getting server info: {resp.status_code}")

    except Exception as e:
        print(f"⚠️ Error in get_real_server_info: {e}")

    return version_detectada, protocolo

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

def start_server():
    global is_waking, wake_start_time
    with lock:
        if is_waking:
            return
        print("Wake signal received! Starting server...")
        is_waking = True
        # Inicio del cronómetro de arranque
        wake_start_time = time.time()
    
    try:
        url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/start_server"
        # Guardamos la respuesta en 'resp'
        resp = requests.post(
            url,
            headers=get_headers(),
            verify=False,
            timeout=5,
        )
        # Imprimimos si salió bien o mal
        if resp.status_code != 200:
            print(f"❌ FALLO AL INICIAR: Código {resp.status_code}")
            print(f"Respuesta Crafty: {resp.text}")
        else:
            print("✅ Comando de inicio enviado correctamente a Crafty.")
            
    except Exception as e:
        print(f"Failed to start: {e}")

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

# --- MINECRAFT PROTOCOL HANDLERS ---
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
    global is_waking, wake_start_time, startup_estimate
    try:
        packet_len = read_varint(conn)
        packet_id = read_varint(conn)
        
        if packet_id == 0x00:
            proto_ver = read_varint(conn)
            addr_len = read_varint(conn)
            conn.recv(addr_len) 
            conn.recv(2)
            next_state = read_varint(conn)

            if next_state == 1:  # STATUS PING
                # Mensaje dinámico según el estado de arranque
                if is_waking:
                    elapsed = time.time() - wake_start_time if wake_start_time > 0 else 0
                    remaining = max(0, startup_estimate - int(elapsed))
                    mins, secs = divmod(remaining, 60)
                    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    motd_text = f"§e⚙️ Starting... (§6{time_str} left§e)\n§fRefining estimate..."
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

            elif next_state == 2:  # LOGIN ATTEMPT
                start_server()

                # Mensaje dinámico según lo que llevemos esperando
                if is_waking:
                    elapsed = time.time() - wake_start_time if wake_start_time > 0 else 0
                    remaining = max(0, startup_estimate - int(elapsed))
                    mins, secs = divmod(remaining, 60)
                    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    kick_text = (
                        f"§6⚙️ Server is starting...\n\n"
                        f"§fEst. remaining: §e{time_str}\n"
                        f"§7We are learning your server speed!"
                    )
                else:
                    kick_text = "§bWake signal sent!\n§7Server will be ready soon."

                msg = {"text": kick_text}
                kick_payload = b"\x00" + pack_string(json.dumps(msg))
                send_packet(conn, kick_payload)

    except Exception as e:
        # print(f"Handshake error: {e}") # Silencio para no ensuciar logs
        pass
    finally:
        time.sleep(0.5)
        conn.close()

def run_fake_server():
    """Escucha en 25565 PERO devuelve el control periódicamente."""
    # CORRECCIÓN CRÍTICA: Bindear y cerrar el socket en cada ciclo es más seguro
    # para evitar conflictos con socat, o usar timeout para salir.
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', LISTEN_PORT))
            s.listen(5)
            s.settimeout(2) # Tiempo máximo de escucha antes de volver a chequear API
            
            # Intentamos aceptar conexiones durante un breve periodo
            start_wait = time.time()
            # Escuchamos solo por 5 segundos antes de salir para chequear estado
            while (time.time() - start_wait) < 5: 
                try:
                    conn, addr = s.accept()
                    t = threading.Thread(target=handle_client, args=(conn,))
                    t.start()
                except socket.timeout:
                    # Nadie se conectó, salimos del while para checkear API
                    break 
                except Exception as e:
                    print(f"Socket error: {e}")
                    break
    except Exception as e:
        print(f"Error binding fake server: {e}")
        time.sleep(1) # Evitar spam si el puerto está ocupado

# --- MAIN LOOP ---
def main():
    global last_active_time, is_waking, wake_start_time
    print("--- Smart Manager Started (Auto-Learning) ---")
    
    was_ready = False # Para detectar transiciones OFF -> ON
    failed_checks = 0 # Para evitar desconexiones por lag spikes

    while True:
        status = get_server_status()
        if status is None:
            time.sleep(3)
            continue
        running, players = status
        
        # Check if actually ready (TCP connect)
        server_ready = False
        if running:
             port_open = is_port_open(REAL_SERVER_IP, REAL_SERVER_PORT)
             if port_open:
                 server_ready = True
                 failed_checks = 0
             else:
                 # Si el proxy YA estaba corriendo, damos un margen de error (Lag Spike Protection)
                 with lock:
                     is_proxy_running = proxy_process is not None
                 
                 if is_proxy_running:
                     failed_checks += 1
                     if failed_checks < 3:
                         print(f"⚠️ Port check failed ({failed_checks}/3). Ignoring temporarily...")
                         server_ready = True # Mantenemos vivo el proxy
                     else:
                         print("❌ Server confirmed dead after 3 failures.")
                         server_ready = False
                 else:
                     # Si no estaba corriendo, simplemente no está listo
                     server_ready = False
        
        if server_ready:
            # Detectar si acaba de encenderse (Transición OFF -> ON)
            if not was_ready:
                print("✨ Server detected as JUST READY. Resetting idle timer.")
                last_active_time = time.time()
                failed_checks = 0 # Reset lag protection counter

            # 1. El servidor REAL está encendido Y escuchando
            if is_waking:
                print("Server detected as ONLINE!")

                # --- CÁLCULO Y APRENDIZAJE ---
                with lock:
                    if wake_start_time > 0:
                        actual_duration = time.time() - wake_start_time
                        print(f"Server took {int(actual_duration)}s to start.")
                        save_startup_time(actual_duration)
                        wake_start_time = 0  # Reset
                # -----------------------------

                    is_waking = False
                last_active_time = time.time()
            
            start_proxy() # Se asegura que socat esté corriendo
            
            if players > 0:
                last_active_time = time.time()
            elif (time.time() - last_active_time) > IDLE_TIMEOUT:
                stop_server()
                stop_proxy()
                time.sleep(10) # Dar tiempo a que se apague
            
            # CORRECCIÓN CRÍTICA: Esperar para no saturar CPU/API
            time.sleep(5) 
            
        else:
            # 2. El servidor REAL está apagado O cargando (puerto cerrado)
            stop_proxy() # Asegurar que socat muere
            
            # Ejecutamos el servidor falso un ratito y volvemos
            run_fake_server()
            
            if is_waking:
                # Si estamos esperando que arranque, no spameamos logs, solo esperamos
                pass

        # Actualizamos el estado anterior para la siguiente iteración
        was_ready = server_ready

if __name__ == "__main__":
    main()
