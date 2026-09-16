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
3. Find **Stroompeil** in the list and click **Install**.
4. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Stroompeil** and select it.
3. Select the **Environment** (only Test is available for now).
4. Enter the **Agent token** you received when provisioning this host in the
   Stroompeil dashboard. The server URL is pre-filled based on the selected
   environment.
5. Submit. The integration connects and registers this instance as a host using
   your Home Assistant location name.

The integration reconnects automatically with backoff if the connection drops.

## License

MIT — see [LICENSE](LICENSE).
