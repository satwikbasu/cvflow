# Experience

> Source of truth for employment history. Confirmed accurate against the CV (`resume/last_updated_cv.pdf`).

## Software Engineer — Codezin Technology Solutions
- **Dates:** June 2025 – Present
- **Location:** Kolkata, India
- **Responsibilities & impact:**
  - Designed a Docker Swarm multi-server architecture orchestrating OpenNMS, Apache Kafka (multi-broker), PostgreSQL, and 10–20 scalable Minion containers, enabling SNMP monitoring of up to 500K simulated nodes across 3 dedicated VPS in a private LAN.
  - Built a custom Bash SNMP simulation engine using SNMPsim with per-node differentiated data via variation modules, scaling to 500K+ nodes with configurable poller-to-node ratios.
  - Integrated Apache Kafka as the streaming layer between Minions and OpenNMS, tuning broker count, partitions, and replication factor alongside tuned PostgreSQL configurations for sustained high-throughput data flow.
  - Leveraged the OpenNMS REST API to build automated node-down alerting (programmatic notification layer on the monitoring stack).
  - Rebranded the OpenNMS Jetty web application from source within a Maven multi-module project; set up a Jenkins CI/CD pipeline integrated with GitHub for triggered builds.
  - Developed a Python REST API for IPSec tunnel observability: tunnel health, on-demand self-healing (StrongSwan + systemd timers), and multi-service log retrieval.
- **Tech used:** Linux administration, SSH/bare-metal servers, Docker, Docker Compose, Docker Swarm, OpenNMS, SNMPsim, Apache Kafka, PostgreSQL, Maven, Jenkins, Python/Flask, StrongSwan, systemd.
- **Context:** ~6 months tenure; early-stage startup. Development is AI-assisted (code generation) with the user owning architecture, integration, and debugging. *(Context only — not a resume/application bullet.)*
