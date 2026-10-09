"""ModelCatalog – zentrale, versionierbare Metadaten freigegebener lokaler
Modelle (§67, erweitert Phase 3/§8: "andere lokal verfuegbare Modelle
pruefen, datenbasierte Entscheidung").

Modelldaten (Name, Download-Groesse, Kontextlaenge) stammen von der
offiziellen Ollama-Modellseite (https://ollama.com/library/qwen3, Stand
21.08.) - `download_size_gb` fuer `qwen3:4b` zusaetzlich real durch einen
tatsaechlichen `ollama pull` in dieser Session bestaetigt (siehe
ARCHITECTURE.md §66). Bis Phase 3 ausschliesslich Modelle der
`qwen3`-Familie (aktuell über die konfigurierte Ollama-Runtime bezogen,
siehe app/ai_providers/ollama_provider.py) - keine Drittanbieter-/
Fantasiewerte.

PHASE 3, ECHTER BENCHMARK-FUND (31.08./01.09., dieselbe i7-3720QM-CPU-only-
Maschine wie §66): `qwen3:4b` benoetigte fuer denselben einfachen
Zusammenfassungs-Prompt wie in §66 ERNEUT eine extrem lange Zeit (>20
Minuten, Abbruch durch den Nutzer-seitigen Timeout) - der Grund ist
plausibel das "thinking"-Verhalten der qwen3-Modellfamilie (die Ollama-
Modellkarte listet `qwen3` explizit mit `capabilities: ["completion",
"tools", "thinking"]|), das bei manchen Prompts sehr lange interne
Reasoning-Ketten erzeugt, BEVOR ueberhaupt die eigentliche Antwort beginnt.
Im selben echten Test lieferte `qwen2.5:1.5b` (KEINE thinking-Faehigkeit,
laut Modellkarte nur `["completion", "tools"]`) fuer denselben Prompt eine
inhaltlich korrekte, brauchbare deutsche Zusammenfassung in ~37s (kalt,
Modell noch nicht im Ollama-Speicher-Cache) bzw. ~10-11s (warm, Modell
bereits geladen) - `llama3.2:1b` verweigerte die Aufgabe ganz ("Ich kann
diese Anfrage nicht bearbeiten"). Datenbasiertes Ergebnis: `qwen2.5:1.5b`
ist auf dieser Referenzmaschine dem bisherigen Standardmodell `qwen3:4b`
sowohl bei Geschwindigkeit als auch bei tatsaechlicher Aufgabenerfuellung
klar ueberlegen - siehe `app/config/settings.py::ollama_model` (neuer
Standardwert) und ARCHITECTURE.md §71.

`min_ram_gb`/`recommended_ram_gb` sind eine dokumentierte, konservative
Faustregel (Download-/Diskgroesse als Naeherung fuer den GGUF-Speicherbedarf
im RAM plus Kontext-/OS-Overhead) - AUSDRUECKLICH KEINE von Ollama
veroeffentlichte offizielle Kennzahl (die Modellseite nennt keine
Mindest-RAM-Werte). `expected_performance_class` ist eine RELATIVE,
modellinterne Einordnung (kleinere Parameterzahl = grundsaetzlich
schneller als groessere, bei sonst gleicher Hardware) - KEINE gemessene
Token/Sekunde-Angabe (Vorgabe, woertlich: "Keine frei erfundenen Angaben
wie '20 tok/s'"). Die tatsaechliche, hardwareabhaengige Einordnung
(schnell/ausgewogen/langsam/sehr langsam) berechnet erst
`recommendation.py::RecommendationEngine` aus Modell + Hardware."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RelativePerformanceClass(str, Enum):
    """Rein modellinterne, relative Einordnung - siehe Moduldocstring."""

    FAST = "fast"
    BALANCED = "balanced"
    SLOW = "slow"


@dataclass(frozen=True)
class ModelCatalogEntry:
    model_name: str
    runtime: str
    tag: str
    download_size_gb: float
    context_length: int
    min_ram_gb: float
    recommended_ram_gb: float
    min_vram_gb: float
    recommended_vram_gb: float
    cpu_only_supported: bool
    gpu_supported: bool
    capability_profile: tuple[str, ...]
    strengths: tuple[str, ...]
    limitations: tuple[str, ...]
    expected_performance_class: RelativePerformanceClass
    # Niedrigere Zahl = wird bei gleichwertiger Eignung bevorzugt
    # empfohlen (siehe RecommendationEngine) - z. B. das kleinere von zwei
    # technisch gleichermassen "passenden" Modellen.
    recommendation_priority: int


#: RAM, die Lexono selbst belegt (Anwendung mit Privacy-/NER-Modellen plus WebView2-Oberflaeche),
#: real gemessen im installierten Build (~1,2 GB Prozess + ~0,8 GB WebView2). Ohne diesen
#: Anteil wurde `qwen3:8b` auf einer 16-GB-Maschine als "empfohlen" eingestuft und liess nur
#: ~1,4-2 GB frei (Betriebssystem + Browser + Entwicklungswerkzeuge fuehrten zu Speicherdruck).
_APP_RESERVE_GB = 2.0


def _ram_estimate(download_size_gb: float) -> tuple[float, float]:
    """Konservative Faustregel (siehe Moduldocstring): min = Downloadgroesse
    + ca. 30% Overhead (Kontext/Runtime) + 2 GB OS-Reserve (technisch lauffaehig),
    empfohlen = zusaetzlich 2 GB Sicherheitsmarge UND der Eigenbedarf von Lexono
    (`_APP_RESERVE_GB`) fuer fluessigen Betrieb neben der restlichen Anwendung."""
    minimum = round(download_size_gb * 1.3 + 2, 1)
    recommended = round(download_size_gb * 1.3 + 4 + _APP_RESERVE_GB, 1)
    return minimum, recommended


_QWEN3_CAPABILITIES = (
    "lokale Textvorverarbeitung",
    "Klassifikation",
    "Zusammenfassung",
    "einfache Extraktion",
)
_QWEN3_LIMITATIONS = (
    "keine anspruchsvolle juristische Argumentation",
    "ersetzt nicht die Claude-Textproduktionsschicht",
    "keine eigenstaendige Rechtsberatung/Rechtsentscheidung",
    "'thinking'-Faehigkeit kann bei manchen Prompts zu sehr langen internen "
    "Reasoning-Ketten und dadurch stark verlaengerter Antwortzeit fuehren "
    "(real gemessen: >20 Minuten fuer eine einfache Zusammenfassung auf "
    "CPU-only-Hardware, Phase 3) - fuer den interaktiven Chat-Pfad nur mit "
    "Vorsicht empfehlbar, siehe qwen2.5-Alternative unten",
)

_QWEN25_CAPABILITIES = (
    "lokale Textvorverarbeitung",
    "Klassifikation",
    "Zusammenfassung",
    "einfache Extraktion",
)
_QWEN25_LIMITATIONS = (
    "keine anspruchsvolle juristische Argumentation",
    "ersetzt nicht die Claude-Textproduktionsschicht",
    "keine eigenstaendige Rechtsberatung/Rechtsentscheidung",
    "kleineres Kontextfenster als qwen3 (32K statt bis zu 256K) - für sehr "
    "lange Sachverhalte ggf. nicht ausreichend",
)


def _qwen3_entry(
    tag: str,
    download_size_gb: float,
    context_length: int,
    performance_class: RelativePerformanceClass,
    priority: int,
) -> ModelCatalogEntry:
    min_ram, recommended_ram = _ram_estimate(download_size_gb)
    return ModelCatalogEntry(
        model_name="Qwen3",
        runtime="ollama",
        tag=f"qwen3:{tag}",
        download_size_gb=download_size_gb,
        context_length=context_length,
        min_ram_gb=min_ram,
        recommended_ram_gb=recommended_ram,
        # Grobe, ebenfalls dokumentierte Naeherung: VRAM-Bedarf fuer
        # vollstaendiges GPU-Offloading liegt in der Groessenordnung der
        # Downloadgroesse (quantisiertes GGUF) - proportionaler Aufschlag
        # statt fixer Offset, damit die Schaetzung ueber alle Modellgroessen
        # hinweg konsistent bleibt.
        min_vram_gb=round(download_size_gb * 1.1, 1),
        recommended_vram_gb=round(download_size_gb * 1.3, 1),
        cpu_only_supported=True,
        gpu_supported=True,
        capability_profile=_QWEN3_CAPABILITIES,
        strengths=(
            "läuft rein lokal, keine Cloud-Abhängigkeit für diesen Schritt",
            "unterstützt sowohl CPU-only- als auch GPU-beschleunigten Betrieb",
        ),
        limitations=_QWEN3_LIMITATIONS,
        expected_performance_class=performance_class,
        recommendation_priority=priority,
    )


# Reale Daten von https://ollama.com/library/qwen3 (Stand 21.08.) - siehe
# Moduldocstring. `qwen3:30b` als groesster hier aufgenommener Eintrag
# (Workstation-Klasse) - die noch groesseren Varianten (32b/235b) sind
# kein realistischer Kanzlei-PC-Anwendungsfall und bewusst nicht
# aufgenommen (siehe ARCHITECTURE.md §67, "keine ueberdimensionierte
# Katalogbreite ohne Produktbedarf").
#: Reale Daten aus `ollama pull qwen2.5:1.5b` + `ollama list`/Ollama-API in
#: dieser Session (Phase 3, siehe Moduldocstring) - download_size_gb aus der
#: tatsaechlichen lokalen Modellgroesse (986 MB), context_length aus der
#: Ollama-API-Antwort (`/api/tags`, `context_length: 32768`). Kein
#: `thinking`-Reasoning-Overhead beobachtet - deutlich konsistentere,
#: kuerzere Antwortzeiten als qwen3 bei vergleichbarer Modellgroesse.
_QWEN25_1_5B = ModelCatalogEntry(
    model_name="Qwen2.5",
    runtime="ollama",
    tag="qwen2.5:1.5b",
    download_size_gb=0.99,
    context_length=32_768,
    min_ram_gb=3.3,
    recommended_ram_gb=5.3,
    min_vram_gb=1.1,
    recommended_vram_gb=1.3,
    cpu_only_supported=True,
    gpu_supported=True,
    capability_profile=_QWEN25_CAPABILITIES,
    strengths=(
        "läuft rein lokal, keine Cloud-Abhängigkeit für diesen Schritt",
        "unterstützt sowohl CPU-only- als auch GPU-beschleunigten Betrieb",
        "kein 'thinking'-Overhead - real gemessen deutlich schnellere und "
        "konsistentere Antwortzeiten als vergleichbar große qwen3-Modelle "
        "auf CPU-only-Hardware (Phase 3, ~10-11s warm statt >20 Minuten)",
    ),
    limitations=_QWEN25_LIMITATIONS,
    expected_performance_class=RelativePerformanceClass.FAST,
    recommendation_priority=0,
)

MODEL_CATALOG: tuple[ModelCatalogEntry, ...] = (
    _QWEN25_1_5B,
    _qwen3_entry("0.6b", 0.523, 40_000, RelativePerformanceClass.FAST, priority=1),
    _qwen3_entry("1.7b", 1.4, 40_000, RelativePerformanceClass.FAST, priority=2),
    _qwen3_entry("4b", 2.5, 256_000, RelativePerformanceClass.BALANCED, priority=3),
    _qwen3_entry("8b", 5.2, 40_000, RelativePerformanceClass.BALANCED, priority=4),
    _qwen3_entry("14b", 9.3, 40_000, RelativePerformanceClass.SLOW, priority=5),
    _qwen3_entry("30b", 19.0, 256_000, RelativePerformanceClass.SLOW, priority=6),
)


def get_model_catalog() -> tuple[ModelCatalogEntry, ...]:
    """Einziger Zugriffspunkt auf den Katalog (statt `MODEL_CATALOG`
    projektweit direkt zu importieren) - erlaubt spaeter z. B. eine
    Versionierung/externe Konfigurierbarkeit einzuziehen, ohne
    Aufrufer anzupassen."""
    return MODEL_CATALOG
