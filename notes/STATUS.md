<!-- Budget 3 KB. Ueber 12 KB ist es kein Budgetproblem mehr, sondern ein Sortierfehler:
     dann liegen Fallen, Entscheidungen oder Vorgeschichte hier, die woandershin gehoeren
     (DOKU-STANDARD.md, Regel 4). Wer dauerhaft mehr braucht, setzt die Zahl hier hoch
     und schreibt in einem Halbsatz dazu, warum. -->
# Offene Punkte

Nur was **offen** ist. Erledigtes wird geloescht, nicht abgehakt, die Historie steht in Git.
Ist die Liste leer, ist das der gute Fall.

**Die drei Ueberschriften bleiben stehen, auch wenn eine leer ist.** Sie sind der Grund,
warum dieselbe Sache nicht einmal als Aufgabe und einmal als Bericht dasteht. Je Punkt
hoechstens acht Zeilen, kein Verlauf; was schon versucht wurde, steht in Git.

---

## Wartet auf dich

### Lizenz, Autorenname, Zeitpunkt der Veroeffentlichung: DU
**Was fehlt:** deine Entscheidung zur Lizenz (Vorschlag MIT), zum Autorennamen im Repo und
dazu, wann das Repo oeffentlich wird.
**Warum es haengt:** noetig erst zum ersten Release (M3); bis dahin bleibt das Repo privat.
**Einzelheiten:** `docs/bauplan.md`, Abschnitt „Zielbild und Grenzen"

---

## Kann erledigt werden

### Meilenstein M1: Plugin-Kern und erste Werkzeuge
**Was fehlt:** `plugin/` (HTTP-Server im Nebenthread, Warteschlange auf `onPeriodicUpdate`,
Fenster, Token, Haken fuer Python), `toolbag_mcp/` (MCP-Server ueber stdio, Fokus-Handhabung,
`install|uninstall|verify`), Werkzeugfamilien 1 bis 4 (Diagnose, Szene, Materialien, Baker),
Registrierung fuer Claude Code im Benutzer-Umfang.
**Warum es haengt:** noch kein Code; laut Bauplan 1 bis 2 Sitzungen.
**Abnahme:** der Ketten-Ablauf (status, Uebersicht, Low tauschen, Alpha Map, Rueckseiten, Bake)
laeuft ueber MCP-Werkzeuge an einer Testszene (Kopie, nie in `X:\GameAssets` speichern);
„Claude Bridge" ist dabei aus.
**Einzelheiten:** `docs/bauplan.md`, Abschnitte Architektur, Werkzeugfamilien, Uebernahmeliste,
Meilensteine

---

## Kleinkram und Beobachten

- `mcp`-SDK: 2.x (`from mcp.server import MCPServer`) nehmen oder `mcp<2` pinnen; beim ersten
  Code entscheiden und in `pyproject.toml` festhalten.
