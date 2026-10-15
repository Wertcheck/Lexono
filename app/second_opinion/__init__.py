"""Cloud-Zweitmeinung fuer Dokumenttyp und Fristen - nur die isolierte, netzfreie Grundlage (§43, Stufe 1).

Owner-Entscheidung (15.10.2026): Die Zweitmeinung wird als ergaenzende, manuell je Dokument angeforderte Funktion
weiter evaluiert, zunaechst nur mit Anthropic/Claude. Sie darf lokale Ergebnisse NIE entfernen, ueberschreiben oder
bestaetigen.

Dieses Paket enthaelt ausschliesslich reine Funktionen (Antwortvalidierung, Laengenpruefung, Vergleich, Uebernahme
als NEUER unbestaetigter Vorschlag). Es ist bewusst nirgends eingebunden: kein Netz, kein API-Aufruf, kein
Gateway-Zweck, keine Datenbank, keine UI. Ein Versand bleibt ausschliesslich Sache des ClaudePrivacyGateway; ein
eigener Zweck dafuer ist eine spaetere, eigene Entscheidung.
"""
