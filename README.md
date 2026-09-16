# Stroompeil HA addon

A Home Assistant custom integration that connects a Home Assistant instance to the
Stroompeil HA server. The integration opens a persistent **outbound** WebSocket to the
server, sends a status snapshot every 60 seconds, and receives allowlisted commands
back over the same connection. The Home Assistant instance needs no inbound ports —
only outbound internet.

## Requirements

- Home Assistant 2026.1 or newer
- [HACS](https://hacs.xyz) installed
- A running Stroompeil HA server and an agent token issued by its operator

## Installation

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/stroompeil/ha-addon` as a repository of category
   **Integration**.
3. Find **Stroompeil HA addon** in the list and click **Install**.
4. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Stroompeil HA addon** and select it.
3. Enter:
   - **Server URL** — the base URL of your Stroompeil HA server, e.g.
     `wss://server.example.com`. The integration appends the agent path and token
     automatically.
   - **Token** — the agent token issued by your Stroompeil HA server operator.
   - **Host name** (optional) — a label for this Home Assistant instance.
   - **Location** (optional) — a free-text location tag.
4. Submit. The integration connects and registers this instance as a host.

The integration reconnects automatically with backoff if the connection drops.

## License

MIT — see [LICENSE](LICENSE).
