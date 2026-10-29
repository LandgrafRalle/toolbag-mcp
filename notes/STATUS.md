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

### PyPI ja oder nein: DU
**Was fehlt:** dein Wort, ob das Paket auf PyPI soll (Konto noetig, `uv publish`).
**Warum es haengt:** ohne PyPI gilt fuer alle die Git-Form `uvx --from git+...`, das reicht;
ein PyPI-Name ist aber nicht reserviert, solange niemand veroeffentlicht.
**Einzelheiten:** `docs/entwicklung.md`, Abschnitt „Release-Ablauf"

---

## Kann erledigt werden

### Nachgelagert aus M1 und M2
- Automatischer Re-Bake nach `baker.replace_low` verstehen: die Ausgaben wurden ohne
  `bake()` neu geschrieben (`docs/pitfalls.md`, Baker).
- Testdaten: `part_low_v2` mit nicht ueberlappenden UVs erzeugen, damit der Low-Tausch im
  Bake sichtbar wird (`tests/make_test_assets.py`).
- Toolbag 4 ungetestet; `getMapPath` gibt es erst ab 5.03 (Rueckfall rechnet den Pfad).
- `render.videos` und `render.export_viewer` sind nur gegen das nachgebaute `mset` geprueft,
  nicht an Toolbag (brauchen eine Animation bzw. dauern lang).
- `shadow catcher` und `backdrop` nur ueber `object.create` und `object.set_attributes`
  erreichbar, ohne eigene Werkzeuge; reicht, solange niemand mehr braucht.

---

## Kleinkram und Beobachten

- `mcp`-SDK auf 2.x (`<3`) gepinnt, Python ab 3.10; Toolbags Python ist 3.12.
- Lange Bakes: reisst ein Werkzeugaufruf das Timeout des Clients, in Claude Code
  `MCP_TOOL_TIMEOUT` hochsetzen.
