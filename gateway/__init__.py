"""Lexono-Gateway: eigenständige, deploybare Relay-Komponente (ARCHITECTURE.md §70).

WICHTIG - eigener Deployment-Kontext:

Dieses Paket ist bewusst NICHT Teil von `app/` (der KanzleiAI-Kanzlei-
Anwendung). Es läuft als separater Prozess, auf separater Infrastruktur,
mit einem eigenen `.env.gateway` - niemals demselben Prozess oder derselben
Konfigurationsdatei wie eine Kanzlei-Installation. Der einzige Zweck: den
echten `ANTHROPIC_API_KEY` serverseitig zu halten und ausschließlich
bereits pseudonymisierte, lokal freigegebene Anfragen authentifizierter
Kanzlei-Installationen an Anthropic weiterzureichen - siehe
`ARCHITECTURE.md` §70 für die vollständige Architekturentscheidung und den
Datenfluss.

Der Gateway kennt keine Mandantendaten, keine Dokumentinhalte, keine
Original-Klartexte - er sieht ausschließlich das, was die lokale
Datenschutzkette (Presidio, Pseudonymisierung, Final Payload Gate) bereits
für den Cloud-Versand freigegeben hat.
"""
