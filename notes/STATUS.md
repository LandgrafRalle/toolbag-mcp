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

### Plugin einmal ueber das Menue starten: DU
**Was fehlt:** In Toolbag *Edit, Plugins, Refresh*, dann *Plugins, Toolbag MCP* anklicken und
das Fenster ansehen (Port, Haken „Allow Python execution", Calls, Stop).
**Warum es haengt:** Claude kann keinen Menueeintrag klicken; die Abnahme lief mit dem Plugin
als Startskript. Die Junction liegt seit dem 29.10.2026 im Plugin-Ordner.

### Lizenz, Autorenname, Zeitpunkt der Veroeffentlichung: DU
**Was fehlt:** deine Entscheidung zur Lizenz (Vorschlag MIT), zum Autorennamen im Repo und
dazu, wann das Repo oeffentlich wird.
**Warum es haengt:** noetig erst zum ersten Release (M3); bis dahin bleibt das Repo privat.
**Einzelheiten:** `docs/bauplan.md`, Abschnitt „Zielbild und Grenzen"

---

## Kann erledigt werden

### Meilenstein M2
**Was fehlt:** Werkzeugfamilien 5 bis 7 (Licht, Sky, Fog, Kamera; Render; `execute_python`
ist schon da), ein vollstaendigeres `fake_mset` fuer Tests der Aktionen ohne Toolbag,
`docs/pitfalls.md` fortschreiben, `doctor` um die Punkte aus der Abnahme erweitern.
**Einzelheiten:** `docs/bauplan.md`, Werkzeugfamilien und Meilensteine

### Nachgelagert aus M1
- Automatischer Re-Bake nach `baker.replace_low` verstehen: die Ausgaben wurden ohne
  `bake()` neu geschrieben (`docs/pitfalls.md`, Baker).
- Testdaten: `part_low_v2` mit nicht ueberlappenden UVs erzeugen, damit der Low-Tausch im
  Bake sichtbar wird (`tests/make_test_assets.py`).
- Toolbag 4 ungetestet; `getMapPath` gibt es erst ab 5.03 (Rueckfall rechnet den Pfad).

---

## Kleinkram und Beobachten

- `mcp`-SDK auf 2.x (`<3`) gepinnt, Python ab 3.10; Toolbags Python ist 3.12.
- Lange Bakes: reisst ein Werkzeugaufruf das Timeout des Clients, in Claude Code
  `MCP_TOOL_TIMEOUT` hochsetzen.
