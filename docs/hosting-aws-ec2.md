# Hosting cvflow: AWS EC2 as a stepping stone

> **Status:** planning reference for **Phase 7+** (when Hermes must run always-on) and **Phase 11** (deploy).
> The authoritative deploy runbook lands in `docs/deploy.md` during Phase 11; this doc explains the
> *hosting strategy* and gives exact EC2 provisioning steps so the first hosted run is safe and predictable.

## Why a server is needed at all

cvflow is **autonomous and always-on**: it discovers jobs daily, holds a Telegram chat, and waits for the
human approval gate at any hour. Phases 0–6 are pure local Python (tests, mocked APIs) — **no server needed**.
The moment Hermes enters the picture (**Phase 7**: Telegram + scheduling + browser + LLM brain routing), the
runtime has to be up 24/7, which a laptop on two local dev machines can't be. That's when hosting starts.

The repo is built to **deploy by `git clone`** (PII profile + master resume are committed; only `config.yaml`,
`data/`, `logs/`, and the CV PDF are gitignored), so moving between hosts is cheap.

## Why AWS free-tier is a stepping stone, NOT the permanent home

The new AWS Free Tier (credit model, mid-2025) grants ~**$100 on signup** (up to ~$200 with activities),
valid **6 months or until the credits are exhausted — whichever comes first**. After that the same box bills at
the normal on-demand rate.

- A suitably-sized instance for cvflow (**`t3.small`, 2 GB**) + a 30 GB disk costs **~$15–18/month** on-demand.
- $100 / 6 months ≈ **$16/month** of headroom → the credits comfortably cover the **entire build-and-test
  window (Phases 7–12)**, but they **expire**. This is a sandbox to learn deployment and run the first live
  cycles safely, not a forever home.

**The project's hard constraint is zero ongoing SaaS/per-call cost; the only allowed standing cost is the server.**
A ~$15/mo AWS box technically satisfies "the server," but it conflicts with the spirit once the credits run out.
So line up a truly-free permanent host and migrate before the credits expire:

| Host | Spec | Cost | Role |
|---|---|---|---|
| **AWS EC2 `t3.small`** | 2 vCPU / 2 GB | ~$15/mo, covered by credits for ~6 mo | **Stepping stone** — learn deploy, run Phases 11–12 |
| **Oracle Cloud "Always Free"** | Ampere ARM up to 4 vCPU / 24 GB | **Free forever** | **Recommended permanent home** — far roomier |
| Hetzner / other VPS | 2 vCPU / 4 GB | ~€4/mo | Fixed-price fallback |

Because deploy = `git clone` + `config.yaml`, migrating AWS → Oracle later is a ~30-minute repeat of the steps below.

## Sizing rationale (why `t3.small`, not `micro`)

The LLMs are **remote** (NIM brain + Gemini tailoring are API calls — no local GPU/RAM). The server only runs
**Hermes + headed Chromium under xvfb + LaTeX + SQLite**. Headed Chromium is the RAM hog:

- `t2.micro`/`t3.micro` (1 GB) → too tight; headed Chromium will OOM/thrash. **Avoid.**
- **`t3.small` (2 GB) → the sweet spot.**
- `t3.medium` (4 GB) → comfortable but ~$30/mo, burns credits in ~3 months.
- Disk: a **20–30 GB gp3** root volume (Tectonic keeps LaTeX small; would be tighter with `texlive-full`).

## The cost-safety rules (avoid the Elastic IP trap)

A past EC2 attempt blew up on costs due to an **Elastic IP**. The rules that prevent a repeat:

1. **Never allocate an Elastic IP.** cvflow uses **Telegram long-polling = outbound-only**; there is **no
   inbound service**, so no static/public-facing IP is ever required. An *unattached* Elastic IP bills while
   idle and survives termination — that is the classic surprise charge.
2. **Use the auto-assigned public IPv4** instead. Since Feb 2024 AWS bills all public IPv4 (~$0.005/hr ≈
   $3.6/mo), but an **auto-assigned** address is **released automatically when you stop/terminate** the
   instance — it cannot become a dangling charge. Your credits absorb the ~$3.6/mo.
3. **Never create a NAT Gateway** (~$32/mo). A public-subnet instance with an auto-assigned IP has outbound
   internet without NAT.
4. **Set AWS Budget alarms** ($5 and $15/mo) the moment the account exists — free, email alerts, would have
   caught the last incident.
5. **Stop the instance when not testing** — you then pay only for the EBS volume (~$2.5/mo for 30 GB).
6. Prefer **SSM Session Manager / EC2 Instance Connect** for shell access → no inbound SSH port needed at all.

---

## Exact provisioning steps (from zero to Hermes running)

> Do these in the AWS region closest to you (e.g. `ap-south-1` Mumbai). Replace placeholders in `<...>`.

### 0. Billing guardrails FIRST (before any instance)

1. **Billing → Budgets → Create budget** → *Monthly cost budget* → amount **$15** → alert thresholds at
   **50% and 90%** → email yourself. Optionally add a second budget at **$5**.
2. **Billing → Free Tier** → enable *"Receive Free Tier usage alerts."*

### 1. Launch the EC2 instance

EC2 → **Launch instance**:

- **Name:** `cvflow`
- **AMI:** *Ubuntu Server 24.04 LTS* (x86_64).
- **Instance type:** **`t3.small`**.
- **Key pair:** create one (`cvflow-key`), download the `.pem`, `chmod 400 cvflow-key.pem`.
  (Or skip and use SSM — see step 3b.)
- **Network settings → Edit:**
  - VPC: default. Subnet: any **public** subnet.
  - **Auto-assign public IP: Enable.**  ← *do this; do NOT touch Elastic IPs.*
  - **Security group** (create new, `cvflow-sg`): **Inbound = SSH (22) from My IP only** (or *no inbound
    rules at all* if using SSM). **Leave outbound = allow all** (needed for git/apt/APIs/Telegram).
- **Storage:** root volume **30 GB gp3**.
- **Advanced → IAM instance profile:** attach a role with **`AmazonSSMManagedInstanceCore`** (enables SSM
  Session Manager shell — optional but recommended).
- **Launch instance.** Verify on the instance page that **no Elastic IP** is associated (only an
  "auto-assigned" public IPv4).

### 2. Connect

```bash
# via SSH (auto-assigned IP shown in the console)
ssh -i cvflow-key.pem ubuntu@<PUBLIC_IPV4>
# OR, with the SSM role attached: EC2 → Connect → Session Manager (no open port, no key)
```

### 3. Base system dependencies

```bash
sudo apt update && sudo apt -y upgrade
# Python + build basics
sudo apt -y install python3.11 python3.11-venv python3-pip git
# Headed-browser support + virtual display for Playwright (Goals 5,6,7)
sudo apt -y install xvfb
# LaTeX: use Tectonic (single self-contained binary, ~tens of MB) instead of
# texlive-full (~5 GB). Tectonic downloads only the packages the resume actually
# uses on first compile, then caches them — local, free, private (no PII leaves).
curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh
sudo mv tectonic /usr/local/bin/
```

> **Why not `texlive-full` / a hosted LaTeX API / Overleaf?** `texlive-full` wastes ~5 GB on the small box.
> Hosted LaTeX→PDF APIs are mostly paid/rate-limited **and would ship resume PII to a third party** (breaks
> both the zero-cost invariant and the privacy posture); Overleaf has no real public compile API. Tectonic
> keeps compilation **local, free, and private** with a tiny footprint. Set `resume.latex_compiler: "tectonic"`
> in `config.yaml` (Phase 6 wires the compile call). Fallback if you prefer apt: `texlive-latex-recommended`
> + `texlive-fonts-recommended` + `texlive-latex-extra` (~1–2 GB) instead of the full distribution.

### 4. Dedicated unprivileged user (security posture)

```bash
sudo adduser --system --group --home /opt/cvflow cvflow
sudo mkdir -p /opt/cvflow && sudo chown cvflow:cvflow /opt/cvflow
```

### 5. Clone the repo and install the Python app

```bash
sudo -u cvflow -H bash
cd /opt/cvflow
git clone <YOUR_PRIVATE_REPO_URL> repo   # use a deploy key / PAT for the private repo
cd repo
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps        # may need sudo; installs Chromium's system libs
```

### 6. Configuration & secrets (never committed)

```bash
cp config.example.yaml config.yaml
# Fill in the real values (Telegram token + authorized_user_id, NIM key, Gemini key, emails).
nano config.yaml
chmod 600 config.yaml
# Confirm the committed PII is present (deploy-by-clone):
ls profile/ resume/master.tex
```
The Fernet key for token/cookie encryption is generated into `data/.fernet_key` (chmod 600) on first run.

### 7. Install & configure Hermes (the substrate — Phase 7)

> Hermes provides Telegram + scheduling + browser runtime + LLM brain routing; it is installed
> **separately** (single-curl installer), **not** via pip.

```bash
# Install Hermes per its current installer (single-curl). Then:
hermes model    # configure the brain: NVIDIA NIM, base_url https://integrate.api.nvidia.com/v1,
                #   model meta/llama-3.3-70b-instruct, api_key = your nvapi-... key
# Configure the Telegram interface with the bot token + the single authorized user id.
# Register cvflow's skills (discovery / analysis / resume / storage / approval-gate) with Hermes.
```
(Exact Hermes commands are pinned in `docs/deploy.md` at Phase 11 once the integration is built.)

### 8. Run headed Chromium under a virtual display

```bash
# Hermes/automation runs the browser headed under xvfb on the server:
xvfb-run -a hermes serve     # placeholder; real entrypoint defined in Phase 11
```

### 9. systemd unit (auto-restart, runs as the cvflow user) — Phase 11

`/etc/systemd/system/cvflow.service` (final form documented in `docs/deploy.md`):

```ini
[Unit]
Description=cvflow (Hermes substrate)
After=network-online.target
Wants=network-online.target

[Service]
User=cvflow
Group=cvflow
WorkingDirectory=/opt/cvflow/repo
Environment=DISPLAY=:99
# Start xvfb + hermes; auto-restart on failure
ExecStart=/usr/bin/xvfb-run -a /opt/cvflow/repo/.venv/bin/hermes serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cvflow
sudo systemctl status cvflow
journalctl -u cvflow -f      # watch logs
```

### 10. Verify, then conserve

- Confirm Hermes replies on Telegram and (Phase 7 exit) does a tool-call round-trip to a trivial cvflow skill.
- When not actively testing: **stop the instance** (EC2 → Instance state → Stop) to preserve credits;
  you keep paying only the small EBS cost. Auto-assigned IP changes on restart — fine (no inbound service).

### Migration off AWS (before credits expire)

Repeat steps 1–9 on Oracle Cloud Always Free (note: Ampere = **ARM**, so Playwright/TeX install the ARM
builds — otherwise identical). Copy over `config.yaml` and `data/` (DB, Fernet key, auth state). Done.

## Cost checklist (pin this)

- [ ] Budget alarms set ($5 / $15) **before** launching.
- [ ] **No Elastic IP** allocated — auto-assigned public IPv4 only.
- [ ] **No NAT Gateway.**
- [ ] Security group inbound = SSH-from-my-IP or none (SSM).
- [ ] Instance **stopped** when idle.
- [ ] Plan migration to Oracle Always Free before the 6-month credit window closes.
