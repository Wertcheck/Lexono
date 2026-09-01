# Lexono AI Gateway – Deployment-Runbook

Dieses Dokument beschreibt die Inbetriebnahme des Lexono-Gateway
(`gateway/`, ARCHITECTURE.md §70) auf einem eigenständigen Linux-VPS. Es ist
eine **Vorlage** – es wurde noch **kein** Server eingerichtet, noch **kein**
echter Anthropic-Key verwendet, noch **keine** produktive Kanzlei-Credential
angelegt. Jeder Platzhalter (`<...>`) muss vor der tatsächlichen Nutzung
durch einen echten Wert ersetzt werden – **niemals** in diesem Dokument oder
irgendeiner Datei im Repository.

## Architekturprinzip (Erinnerung)

```
Lexono-Client (Kanzlei-PC) → HTTPS → Lexono AI Gateway (dieser Server) → Anthropic API
```

Auf diesem Server laufen **ausschließlich** Auth-/Rate-Limit-/Relay-Logik.
**Ausdrücklich NICHT** auf diesem Server: Lexono-Backend, Lexono-Datenbank,
Dokumentenspeicherung, Chat-Historien, lokale KI, sonstige Kanzleidaten.

## 0. Voraussetzungen

- Hetzner-Cloud-Server, Region Deutschland (Nürnberg/Falkenstein), Ubuntu
  LTS, 2 vCPU / 4 GB RAM (Zielkonfiguration – nichts hier ist an Hetzner
  gekoppelt, jeder vergleichbare Linux-VPS funktioniert identisch).
- Eine Domain/Subdomain, deren DNS-A-Record auf die IPv4-Adresse des Servers
  zeigt (z. B. `gateway.<kanzlei-domain>.de`) – Voraussetzung für die
  automatische Let's-Encrypt-Zertifikatsausstellung durch Caddy.
- SSH-Zugriff mit einem bereits hinterlegten SSH-Public-Key (kein
  Passwort-Login).

## 1. Server-Grundhärtung

```bash
# Als root, direkt nach der Server-Erstellung:
apt update && apt upgrade -y
apt install -y ufw fail2ban unattended-upgrades

# Nicht-root Deploy-/Betriebs-User anlegen
adduser --disabled-password --gecos "" lexono-gateway
usermod -aG sudo lexono-gateway   # nur falls sudo-Rechte für den Betrieb nötig sind

# SSH-Härtung (/etc/ssh/sshd_config):
#   PasswordAuthentication no
#   PermitRootLogin no
systemctl restart ssh

# Firewall: NUR SSH, HTTP (ACME-Challenge), HTTPS öffentlich. Der
# Gateway-Port 8700 wird NIE freigegeben - der Prozess bindet ohnehin nur
# an 127.0.0.1 (siehe gateway/config.py).
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable

# fail2ban schützt SSH gegen Brute-Force (Standardkonfiguration reicht)
systemctl enable --now fail2ban

# Automatische Sicherheitsupdates
dpkg-reconfigure -plow unattended-upgrades
```

Optional zusätzlich: Hetzner Cloud Firewall als zweite, netzwerkseitige
Schicht mit denselben Regeln (rein additiv, keine Code-/Deployment-Kopplung
an Hetzner-APIs).

## 2. Anwendung deployen

```bash
# Als lexono-gateway-User:
sudo mkdir -p /opt/lexono-gateway
sudo chown lexono-gateway:lexono-gateway /opt/lexono-gateway
cd /opt/lexono-gateway
git clone <REPOSITORY_URL> current
cd current

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
```

`pip install -e .` installiert ausschließlich die in `pyproject.toml`
gelisteten Drittanbieter-Abhängigkeiten (fastapi, uvicorn, sqlalchemy,
argon2-cffi, anthropic, httpx, pydantic-settings – alles bereits Teil der
regulären `[project.dependencies]`, keine gesonderte Gateway-Extras-Gruppe
nötig). `gateway/` selbst wird dabei **nicht** als eigenes Paket installiert
(`[tool.setuptools.packages.find]` ist bewusst auf `app*` begrenzt) –
deshalb startet der Service unten über `python -m uvicorn` statt des
`uvicorn`-Konsolenskripts, siehe Kommentar in `deploy/lexono-gateway.service`.

`gateway_data/` (SQLite-Datei mit Tenant-Metadaten) wird beim ersten Start
automatisch angelegt (`gateway/db.py::_ensure_sqlite_directory_exists`).

## 3. Secrets konfigurieren

**Niemals** `.env.gateway` im Anwendungsverzeichnis/Repository ablegen –
stattdessen an einem vom Deploy-Verzeichnis getrennten, systemd-verwalteten
Ort:

```bash
sudo mkdir -p /etc/lexono-gateway
sudo touch /etc/lexono-gateway/.env.gateway
sudo chown lexono-gateway:lexono-gateway /etc/lexono-gateway/.env.gateway
sudo chmod 600 /etc/lexono-gateway/.env.gateway
```

Inhalt von `/etc/lexono-gateway/.env.gateway` (Werte manuell, **nie** über
Git, **nie** in einer Beispieldatei mit echtem Wert):

```ini
# NUR ein Platzhalter - der echte Wert wird ausschliesslich manuell auf
# dem Server eingetragen, siehe Abschnitt "API-Key-Rotation" unten.
ANTHROPIC_API_KEY=<ANTHROPIC_API_KEY_PLATZHALTER>

# Zentrale Modellsteuerung (Umsetzungsplan Punkt 1) - Modellwechsel = Wert
# hier aendern + Service neu starten, kein Client-Rebuild.
DEFAULT_MODEL=claude-sonnet-5
ALLOWED_MODELS=["claude-sonnet-5", "claude-opus-4-8"]

MAX_TOKENS_CEILING=4000
MAX_REQUEST_BYTES=200000
DEFAULT_RATE_LIMIT_PER_MINUTE=30

DATABASE_URL=sqlite:///./gateway_data/gateway.db

HOST=127.0.0.1
PORT=8700
LOG_LEVEL=INFO
```

Siehe auch `.env.gateway.example` im Repository (dort ebenfalls nur
Platzhalter/Defaults, wie bisher).

## 4. systemd-Service einrichten

```bash
sudo cp deploy/lexono-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lexono-gateway
sudo systemctl status lexono-gateway
```

`--workers 1` ist beabsichtigt (siehe Kommentar in der Unit-Datei) – das
Rate-Limiting hält seinen Zustand im Prozessspeicher (Pilotphase-Architektur,
ARCHITECTURE.md §70).

## 5. Reverse Proxy (Caddy) einrichten

```bash
# Caddy-Installation gemäß offizieller Anleitung (apt-Repository), dann:
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/<GATEWAY_DOMAIN>/gateway.<kanzlei-domain>.de/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy stellt automatisch ein Let's-Encrypt-Zertifikat aus, sobald DNS korrekt
zeigt. Alternative nginx-Konfiguration (falls bevorzugt):

```nginx
server {
    listen 443 ssl;
    server_name gateway.<kanzlei-domain>.de;

    ssl_certificate     /etc/letsencrypt/live/<domain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<domain>/privkey.pem;

    client_max_body_size 250k;   # analog zu Caddys request_body max_size

    location / {
        proxy_pass http://127.0.0.1:8700;
        proxy_set_header Host $host;
    }
}
```

(TLS-Zertifikat bei nginx separat über `certbot` einrichten.)

## 6. Verifikation

```bash
curl https://gateway.<kanzlei-domain>.de/health
# erwartet: {"status": "ok"}
```

Ein echter End-to-End-Test (mit einer erst hier angelegten Test-Kanzlei-
Credential, siehe Abschnitt 7) erfordert einen bereits eingetragenen echten
`ANTHROPIC_API_KEY` – **nicht Teil dieser Vorlage**, erst nach expliziter
Freigabe des echten Serverbetriebs durchzuführen.

## 7. Erste Kanzlei-Credential anlegen

```bash
cd /opt/lexono-gateway/current
GATEWAY_TENANT_NAME="Kanzlei Mustermann" .venv/bin/python scripts/create_gateway_tenant.py
```

Gibt `client_id` und das Klartext-`client_secret` **einmalig** aus. Beide
Werte in der betroffenen Kanzlei-`.env` eintragen:
`LEXONO_GATEWAY_URL=https://gateway.<kanzlei-domain>.de`,
`LEXONO_GATEWAY_CLIENT_ID=<client_id>`,
`LEXONO_GATEWAY_CLIENT_SECRET=<client_secret>`.

## 8. API-Key-Rotation (zentraler Anthropic-Key)

Kein Codechange, kein Client-Rebuild nötig – reine Server-Operation:

1. Neuen Key im Anthropic-Konto erzeugen (alten dort ggf. parallel noch
   aktiv lassen, bis Schritt 3 verifiziert ist).
2. `/etc/lexono-gateway/.env.gateway`: `ANTHROPIC_API_KEY=<neuer Wert>`.
3. `sudo systemctl restart lexono-gateway`.
4. `curl https://.../health` + ein echter Testaufruf einer bestehenden
   Kanzlei-Credential verifizieren.
5. Erst danach den alten Key im Anthropic-Konto widerrufen.

Keine feste Rotationsfrist vorgeschrieben (wie im Architekturplan
gefordert) – Rotation bei Verdacht auf Kompromittierung **sofort**, sonst
nach eigenem Ermessen.

## 9. Kanzlei-Credential widerrufen / rotieren

Bei Verdacht auf ein kompromittiertes Kanzlei-Credential – **betrifft
ausschließlich diese eine Kanzlei**, alle anderen und der zentrale
Anthropic-Key bleiben unberührt:

```bash
# Sofortiger Widerruf (Kanzlei kann sich danach nicht mehr authentifizieren):
GATEWAY_TENANT_CLIENT_ID="<client_id>" .venv/bin/python scripts/revoke_gateway_tenant.py

# Alternative: nur das Secret rotieren (client_id bleibt, altes Secret
# wird sofort ungueltig, neues Secret muss in der Kanzlei-.env hinterlegt
# werden):
GATEWAY_TENANT_CLIENT_ID="<client_id>" .venv/bin/python scripts/rotate_gateway_tenant_secret.py
```

## 10. Update-Prozess / Rollback

```bash
cd /opt/lexono-gateway/current
git fetch
git checkout <neuer-tag-oder-commit>
.venv/bin/pip install -e ".[gateway]"
sudo systemctl restart lexono-gateway
curl https://.../health   # verifizieren
```

Rollback: `git checkout <vorheriger-tag-oder-commit>` + erneuter Restart.
Kurzer Restart (Sekunden) ist für die Pilotgröße akzeptabel – kein
Zero-Downtime-Deployment nötig (spätere Erweiterung, falls relevant).

## 11. Backup

Einzige zu sichernde Datei: `gateway_data/gateway.db` (Tenant-Metadaten:
Name, `client_id`, Secret-**Hash**, Rate-Limit, aktiv/inaktiv – keine
Mandantendaten, keine Inhalte). Täglicher Kopiervorgang an einen zweiten Ort
reicht:

```bash
0 3 * * * cp /opt/lexono-gateway/current/gateway_data/gateway.db /backup/lexono-gateway/gateway-$(date +\%F).db
```

Verlust wäre unangenehm (Kanzlei-Credentials neu ausstellen), nicht
kritisch (keine Kanzleidaten betroffen).

## 12. Monitoring

`/health` periodisch prüfen (externer Uptime-Check oder einfacher
Cron+curl+Alert). Anwendungs-Logs laufen über `journalctl -u
lexono-gateway` – strukturell inhaltsfrei (siehe `gateway/logging_utils.py`:
nur `request_id`/`tenant_id`/`duration_ms`/`status`/`error_category`/
`input_tokens`/`output_tokens`, niemals Prompt-/Antworttext).

## 13. Vor echtem produktivem Kanzleibetrieb zusätzlich zu klären

Diese Punkte sind bewusst **nicht** Teil dieser Code-/Deployment-Vorlage,
sondern rechtlich/organisatorisch zu bewerten, bevor echte Mandantendaten
über diesen Server laufen: AVV/Auftragsverarbeitungsvertrag mit Anthropic,
Nachweis Serverstandort Deutschland/EU, Log-Retention-Policy für den
Gateway selbst (der Client hat bereits einen `retention_days`-Platzhalter,
der Gateway noch nicht), Incident-Response-Runbook, Dependency-
Vulnerability-Scanning, ggf. eine formale Sicherheitsprüfung (Pentest).
