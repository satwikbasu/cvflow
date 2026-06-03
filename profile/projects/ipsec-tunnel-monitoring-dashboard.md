# IPSec Tunnel Monitoring Dashboard

- **Repo:** https://github.com/satwikbasu/tunnel-monitor-dashboard
- **What it is:** A real-time web monitoring station for IPSec tunnels — displays live tunnel status, log streams, and one-click remediation controls, backed by a custom REST API with on-demand self-healing.
- **Frontend:** React + Vite + TypeScript UI (MUI + Radix/shadcn-ui components), TanStack Query; initial design via AI-assisted Figma-Make, then integrated with the backend.
- **Backend / ops (under `artifacts/`):** Python services — `tunnel-api` (health + log retrieval REST API), `tunnel-collector`, `tunnel-monitor` (with a systemd timer), and `tunnel-recovery-alert` for node-down/recovery notifications; self-healing via StrongSwan + systemd timers; nginx reverse proxy; `install.sh` to deploy the full stack.
- **Tech:** React, TypeScript, Vite, MUI, Radix/shadcn-ui, TanStack Query; Python REST API; StrongSwan, systemd (services + timers), nginx, Linux.
- **Highlights:** End-to-end observability + remediation for IPSec tunnels; data-plane derived state as single source of truth; packaged deployment artifacts.
- **Note:** Appears on the CV as the "IPSec Tunnel Monitoring Dashboard" project.
