"""Kostenfreie Zugangspruefung fuer den Modellvergleich (P2.2 Phase 2): ruft nur die Modell-Liste der Anbieter ab.
Liest die Schluessel aus der lokalen .env (ANTHROPIC_API_KEY, OpenAI_API_KEY), gibt sie NIE aus - nur, ob vorhanden.
Aufruf: python -m scripts.legal_bench.cloud_access_check"""

from __future__ import annotations

from pathlib import Path

import httpx

ENV = Path(__file__).resolve().parents[2] / ".env"


def keys() -> dict[str, str | None]:
    """Nur diese beiden Variablen; Werte verlassen diese Funktion nur an die HTTP-Header."""
    from dotenv import dotenv_values

    values = dotenv_values(ENV)
    return {"anthropic": values.get("ANTHROPIC_API_KEY"), "openai": values.get("OpenAI_API_KEY") or values.get("OPENAI_API_KEY")}


def main() -> None:
    k = keys()
    print("Schluessel vorhanden:", {name: bool(value) for name, value in k.items()})
    if k["anthropic"]:
        r = httpx.get("https://api.anthropic.com/v1/models", timeout=20,
                      headers={"x-api-key": k["anthropic"], "anthropic-version": "2023-06-01"})
        ids = [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
        print("Anthropic:", r.status_code, ids[:20])
    if k["openai"]:
        r = httpx.get("https://api.openai.com/v1/models", timeout=20, headers={"Authorization": f"Bearer {k['openai']}"})
        ids = sorted(m["id"] for m in r.json().get("data", [])) if r.status_code == 200 else []
        chat = [i for i in ids if i.startswith(("gpt-5", "gpt-4.1", "gpt-4o", "o3", "o4"))]
        print("OpenAI:", r.status_code, chat[:40])


if __name__ == "__main__":
    main()
