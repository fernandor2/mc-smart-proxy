# Minecraft Smart Proxy

A lightweight, intelligent proxy container designed to run alongside [Crafty Controller](https://craftycontrol.com/). It allows your Minecraft server to "sleep" when no one is online and wake up automatically when a player tries to join, saving resources while keeping the server accessible 24/7.

## 🚀 Features

*   **Auto-Wake:** Starts the Minecraft server automatically when a player connects to the proxy port.
*   **Auto-Sleep:** Stops the server after a configurable idle time (no players online).
*   **Smart Feedback:** Kicks the player with a dynamic "Server is starting..." message that includes an estimated remaining time based on previous boot times.
*   **MOTD Integration:** Displays status in the server list (e.g., "Sleeping" or "Starting...").
*   **Auto-Learning:** Learns how long your specific server takes to boot to provide accurate countdowns.
*   **Robustness:** 
    *   Disconnects gracefully if the server takes too long to start (prevents zombie states).
    *   Falls back to direct ping checks if the Crafty API becomes unreachable.
    *   Wait for the server to be *fully* ready (mods loaded) before forwarding traffic.

## 🛠️ Setup & Configuration

This project is designed to run via **Docker Compose**.

### 1. Prerequisites
*   Docker & Docker Compose installed.
*   A running instance of **Crafty Controller**.
*   An API Token from Crafty (Settings -> API Keys).

### 2. Configuration (`docker-compose.yml`)

Create or edit your `docker-compose.yml` file. You need to configure the environment variables to match your setup.

```yaml
services:
  mc-smart-proxy:
    build: .
    container_name: minecraft_smart_proxy
    restart: unless-stopped
    ports:
      - "25565:25565" # Public Port
    environment:
      # URL of your Crafty Controller (Use internal IP if possible)
      - CRAFTY_URL=http://192.168.1.10:8443
      
      # API Token generated in Crafty
      - CRAFTY_TOKEN=YOUR_API_TOKEN_HERE
      
      # The UUID or ID of the server in Crafty (usually '1' if it's the first one)
      - SERVER_ID=1
      
      # Port the proxy listens on (Matched with Docker ports)
      - LISTEN_PORT=25565
      
      # The REAL port your Minecraft server runs on (Must be different from LISTEN_PORT)
      - REAL_SERVER_PORT=25599
      
      # IP of the machine running the Minecraft server (Usually the host machine)
      - REAL_SERVER_IP=192.168.1.10
      
      # Time in seconds to wait before stopping an empty server (Default: 600s / 10m)
      - IDLE_TIMEOUT=600
```

### 3. Running the Proxy

```bash
# Build and start the container
docker-compose up --build -d

# View logs
docker logs -f minecraft_smart_proxy
```

## ⚙️ How it works

1.  **Sleeping State:** When the real server is offline, the Proxy listens on port `25565`.
2.  **Wake Up:** When a player joins, the Proxy sends a "Start" command to Crafty API.
3.  **Feedback:** The player is kicked with a message: "Server is starting... Est time: 45s".
4.  **Monitoring:** The proxy monitors the real server port using a Minecraft Status Ping.
5.  **Ready State:** Once the server is fully loaded (responding to pings), the Proxy launches `socat` to forward all traffic transparently.
6.  **Sleep:** If the server is empty for `IDLE_TIMEOUT` seconds, the Proxy sends a "Stop" command to Crafty and returns to the Sleeping State.

## 🐛 Troubleshooting

*   **"Can't reach server" while starting:** The proxy waits for the server to be *fully* ready (responding to protocol pings) to ensure connection stability. Be patient, it might take a few seconds longer than a raw TCP check.
*   **Infinite "Starting..." loop:** Check the container logs. If the API cannot be reached, ensure `CRAFTY_URL` is reachable from inside the container.
*   **Proxy not stopping:** Ensure `IDLE_TIMEOUT` is set correctly (in seconds).