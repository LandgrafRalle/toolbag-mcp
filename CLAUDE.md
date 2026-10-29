<!-- Budget 4 KB. Voll? Dann hat ein Thema eine eigene Datei unter docs/ verdient,
     auslagern, nicht kuerzen. Randbedingungen bleiben IMMER stehen, auch wenn sie das
     Budget reissen (DOKU-STANDARD.md, Regel 7: Sicherheit schlaegt Budget). -->
# Toolbag-MCP

MCP-Server fuer **Marmoset Toolbag 5**: ein Plugin in Toolbag nimmt Auftraege an, ein
Python-Server aussen bietet sie Claude (Code, Desktop, Cursor) als feste Werkzeuge an: Szene,
Materialien, Baker, Licht, Render. Freier Python-Code laeuft nur, wenn der Nutzer den Haken im
Plugin setzt. Nachfolger der privaten Bruecke `X:\eg\Tools\toolbag-bridge\` (Unreal Tools).
Soll **oeffentlich auf GitHub** erscheinen, ist bis zum ersten Release privat. Stand M1
(29.10.2026): Plugin, Server, Familien 1 bis 4 laufen, Abnahme bestanden; M2 folgt.

## Wo steht was

Diese Datei ist nur der Wegweiser, hier wird kein Inhalt wiederholt.

| Quelle | Zustaendig fuer |
|---|---|
| `docs/bauplan.md` | Zielbild, Architektur (Plugin und Server), die sieben Werkzeugfamilien in Baureihenfolge, belegte Fallen der Toolbag-API, Uebernahmeliste aus der Bruecke, Meilensteine mit Abnahme. **Lesen, bevor Code entsteht oder ein Werkzeug dazukommt** |
| `docs/pitfalls.md` | Belegte Fallen der Toolbag-API und des Plugin-Betriebs. **Lesen, bevor Code gegen `mset` entsteht oder ein Toolbag-Verhalten raetselhaft ist** |
| `README.md` | Dateikarte, Aufrufe (`install`, `doctor`, `call`, Tests), Voraussetzungen; wird zu M3 englisch und zur oeffentlichen Anleitung |
| `notes/STATUS.md` | Was gerade **offen** ist, vor der Arbeit lesen. Erledigtes wird geloescht, die Historie steht in Git |
| `X:\eg\Tools\toolbag-bridge\LIESMICH-api.md` | Belege zu den Fallen, bis sie in `docs/pitfalls.md` stehen (M2). Nur lesen, dort nichts aendern |
| Projektgedaechtnis | Vorlieben und Rueckmeldungen des Nutzers; nichts Offenes, keine Entscheidung zum Repo (die gehoert nach `docs/`) |
| `~/.claude/DOKU-STANDARD.md` | Wie hier dokumentiert wird. Lesen, bevor eine Doku-Datei angelegt, aufgeteilt oder umgebaut wird |

## Randbedingungen

- **Wird oeffentlich.** Nichts aus dem Firmenkontext ins Repo: keine Goliath-Namen, keine
  `X:\GameAssets`-Pfade, keine Skew-Masken, kein `fix_ao`. Jede aus der Bruecke uebernommene
  Datei vorher auf Firmenpfade und Namen pruefen. Name ohne „Marmoset" (Marke von Marmoset LLC).
- **Entscheidungen des Nutzers, nicht meine:** Lizenz (Vorschlag MIT), Autorenname, Zeitpunkt
  der Veroeffentlichung, endgueltiger Projektname. Bis dahin bleibt das Repo privat.
- **Nur ein Plugin darf `onPeriodicUpdate` setzen.** Das Plugin dieses Projekts nie zusammen
  mit „Claude Bridge" oder „Skew Masks" in Toolbag laufen lassen; vor jedem Test die Bruecke
  aus. Die Bruecke in Unreal Tools bleibt unveraendert, bis das neue Plugin den Ketten-Ablauf
  beherrscht (Abnahme M1 im Bauplan); der Umstieg dort ist Sache des Unreal-Tools-Chats.
- **Freier Python-Code nur mit Freigabe des Nutzers:** der Haken im Plugin-Fenster ist
  standardmaessig aus und nicht persistent. Der Server darf ihn nie umgehen oder setzen.
- **Testszenen sind Kopien.** Nie eine Szene in `X:\GameAssets` speichern oder ueberschreiben.
  Testdaten liegen in `~\.toolbag-mcp\test\`, nicht unter `%TEMP%`: AppData ist fuer die
  Claude-App umgeleitet, Toolbag saehe andere Dateien.
- **Toolbag zum Testen:** eine Instanz, gestartet mit dem Plugin als Skriptargument (README),
  beendet mit `toolbag.quit`; nie eine zweite starten, solange eine laeuft. `install` und
  alles, was in `%LOCALAPPDATA%` schreibt, nur von aussen (WMI-Prozess), sonst landet es in
  der Container-Kopie der Claude-App.

## Doku-Pflege

Neues Wissen wird nach `~/.claude/DOKU-STANDARD.md` einsortiert, nicht hier angehaengt.
Waechst diese Datei ueber 4 KB, ist ein Thema reif fuer eine eigene Datei unter `docs/`.
Struktur-Fragen ueber dieses Projekt hinaus gehoeren in einen Chat aus `C:\Dev`.
