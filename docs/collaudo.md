# Calco — foglio di collaudo

Da fare **prima** del primo sopralluogo vero. Segui l'ordine: ogni blocco dà per buono il precedente. Se un punto fallisce, annota cosa è successo (e il testo del pulsante **Log** nell'app desktop) e fermati lì.

Legenda: ☐ da fare · scrivi a fianco OK / KO e una nota.

---

## A. Computer (una volta sola, ~15 min)

- ☐ **A1** Doppio clic su `Start Calco.cmd`. Si apre il browser su `http://localhost:7878` e in basso a sinistra compare **Engine ready** con la scheda video (RTX 3060).
- ☐ **A2** In basso a sinistra c'è il riquadro del telefono con **Network code** e **Password** per l'abbinamento. Se compare in rosso **Wi-Fi transfer blocked by Windows**, apri **How to fix** e segui i passi (PowerShell come amministratore, `tools\allow-wifi.ps1`), poi **I Ran It · Check Again**: la scritta rossa sparisce. *Serve solo per il Wi-Fi: il cavo funziona sempre.*
- ☐ **A3** Clic su **Create a demo capture**. Dopo circa un minuto compare "Synthetic box + cylinder" nella lista.
- ☐ **A4** Qualità **Draft** → **Process**. Finisce in meno di un minuto; il modello ruota col mouse; le misure sono circa 85 × 63 × 83 mm (l'oggetto vero è 80 × 60 × 80: il LiDAR sbaglia di qualche mm, è normale).
- ☐ **A5** Qualità **Medium** → se richiesto **Install OpenMVS**, poi **Process**. Finisce in pochi minuti; misure entro ±1 mm da 80 × 60 × 80; **Scale from: Scan mat**.
- ☐ **A6** Viste **Texture / Clay / Wire** e **Grid** funzionano; tema chiaro/scuro (icona in alto a sinistra) funziona. Il pannello di destra ha tre schede (**Process / Result / Export**) e non scorre.
- ☐ **A7** Scheda **Export**: **3D print**, togli la spunta a 3MF (deve restare tolta), **Export STL** → **Open Export Folder**. Apri lo STL nello slicer: dimensioni in mm corrette, base piatta appoggiata al piano, nessun errore di mesh aperta.
- ☐ **A8** Export **Blender / render** → apri il GLB in Blender: texture visibile, oggetto dritto.
- ☐ **A9** Calibrazione su un lato: scheda **Result**, scegli il lato X, scrivi `100`, **Apply Calibration**. Le misure cambiano in proporzione; **Reset calibration** le riporta com'erano.
- ☐ **A10** Calibrazione su due punti: **Measure** sopra il modello, clic su due punti, compare la distanza; scrivi la lunghezza vera e **Calibrate**.
- ☐ **A11** GPU: se nella scheda **Process** compare **Enable GPU**, premilo (scarica 415 MB una volta; su questo PC è già installato). In basso a sinistra deve esserci **GPU: In use** e, dopo un'elaborazione Medium, nella scheda **Result** **Computed on: GPU**. Durante il calcolo la barra in basso mostra *Computing depth maps (GPU) xx%*.
- ☐ **A12** Prova **High** sulla demo: più lento di Medium (le foto sono usate a 2000 px), misure uguali o migliori. Annota il tempo: `______`.

## B. Tappetino di scansione (una volta sola)

Il tappetino dà la scala esatta. Non è obbligatorio: senza, la scala viene dal LiDAR (errore ~1%).
Si scarica dal pulsante **Scan Mat** in basso a sinistra, in tre misure: **A5** (oggetti fino a ~8 cm), **A4** (fino a ~15 cm), **A3** (fino a ~25 cm).

Scegli **una** delle due strade:

**Strada 1 — carta incollata su un supporto rigido (consigliata)**
- ☐ **B1** Stampa il PDF al **100%** ("dimensioni reali", mai "adatta alla pagina"), meglio su carta adesiva opaca.
- ☐ **B2** Incollalo su qualcosa di rigido e piatto: lastra stampata in 3D da 3 mm, plexiglass, alluminio composito, vetro di una cornice. Niente bolle né pieghe.

**Strada 2 — tutto stampato in 3D**
- ☐ **B1** Scarica lo zip **3D print** (A5 entra in un piatto 220 × 220; A4 serve un piatto da 300).
- ☐ **B2** Due colori (AMS/MMU): `base.stl` bianco + `pattern.stl` nero, caricati come un unico oggetto. Un solo estrusore: `combined.stl` in bianco e cambio filamento a nero a 2,0 mm di altezza. **Filamento opaco**: quello lucido riflette e nasconde i marker.

In entrambi i casi:
- ☐ **B3** Misura col calibro la barra di calibrazione: deve essere **100,0 mm**. Valore misurato: `______ mm`. Se non è 100,0 scrivilo nel campo **Mat bar (mm)** ogni volta che elabori.
- ☐ **B4** Un tappetino leggermente imbarcato dà comunque la scala giusta (l'app se ne accorge, usa le distanze tra marker vicini e te lo segnala), ma piatto è più preciso.

## C. iPhone: installazione

- ☐ **C1** Su GitHub: repository **Calco → Actions → iOS app → ultima esecuzione verde → Artifacts → `Calco-unsigned-ipa`**. Scarica lo zip ed estrai `Calco-unsigned.ipa`.
- ☐ **C2** Installa con Sideloadly. L'app si apre sulla lista vuota "No scans yet".
- ☐ **C3** **New scan → Object**: consenti la **fotocamera**. Si vede l'immagine dal vivo con la griglia bianca del LiDAR che copre le superfici.
- ☐ **C4** L'icona dell'app è il quadrato lime su fondo scuro. Alla prima apertura consenti la **rete locale**.
- ☐ **C5** **Abbinamento** (una volta sola): sul PC, in basso a sinistra, ci sono **Network code** (es. `30AG-0EK`) e **Password** (6 cifre). Sul telefono si apre da solo *Settings → Connect*: scrivili e premi **Connect** → *Connected to …*. Serve sia per il Wi-Fi sia per il cavo: nessun altro PC può ricevere le tue scansioni.
- ☐ **C5b** In alto c'è sempre la barra di connessione: verde = collegato (dice se via **cavo** o **Wi-Fi** e il nome del PC), arancione = abbinato ma il PC non risponde (con il motivo), grigio = non abbinato. Toccala per le impostazioni.
- ☐ **C5c** Password sbagliata → messaggio *Wrong password*. **New** sul PC genera una password nuova: il telefono va riabbinato.

## D. Prima scansione di prova (in casa, oggetto facile)

Oggetto consigliato: una scatola opaca e non lucida, 10–20 cm, di cui conosci le misure col calibro. Buona luce diffusa, niente sole diretto.

- ☐ **D1** Oggetto al centro del tappetino. **New scan → Object**.
- ☐ **D2** Inquadra l'oggetto, **Place box**: compare un cubo con spigoli spessi lime, sempre visibile anche dietro l'oggetto. Regola la dimensione col cursore finché lo contiene tutto con un po' di margine; **Move box** lo risposta dove stai puntando.
- ☐ **D2b** Le superfici già viste dal LiDAR si colorano di un velo lime trasparente: è l'anteprima di cosa è stato scansionato.
- ☐ **D3** Tocca il pulsante rosso: parte lo scatto automatico. A ogni foto senti una **vibrazione** leggera e il bordo dello schermo lampeggia lime. Il contatore sale mentre ti muovi; **non** scatta se stai fermo.
- ☐ **D3b** Scatta solo quando tieni il telefono quasi fermo (meno di ~5 cm/s e ~12°/s): così le foto non vengono mosse. Se vai veloce compare *Slow down*. Il ritmo giusto è: sposta, fermati un attimo, sposta.
- ☐ **D3c** **Dettagli**: il pulsante con il fiore (macro) a sinistra mette in pausa il LiDAR e apre la fotocamera ravvicinata. Avvicinati a 3–15 cm da un foro, una filettatura, una scritta: in alto compare **Macro lens** quando il telefono passa da solo alla lente macro. Scatta da sola quando giri di qualche grado e stai fermo (o col pulsante bianco). **Done** torna alla scansione LiDAR.
- ☐ **D4** Muoviti troppo in fretta: compare **Slow down** e smette di scattare. Rallenta: riprende.
- ☐ **D5** Fai un giro completo ad altezza oggetto, poi un secondo giro dall'alto (~45°). L'anello di copertura in alto si riempie di verde (anello interno = giro basso, esterno = giro alto).
- ☐ **D6** Puntini bianchi restano sospesi nei punti da cui hai scattato.
- ☐ **D7** **Done** con 40–80 foto. La scansione compare nella lista con anteprima e peso in MB.
- ☐ **D8** Prova anche: chiudi con la **X** a metà scansione → chiede conferma → **Discard** cancella tutto.

## E. Invio al computer

**Via cavo (la strada più sicura, niente da configurare)**
- ☐ **E1** Calco aperto sul PC e sul telefono, collega il cavo (il telefono deve fidarsi del PC, come per Sideloadly). Entro 2–3 secondi: sul telefono barra verde **Connected by cable**, sul PC riquadro **Cable** con il nome dell'iPhone.
- ☐ **E2** Le scansioni non ancora inviate partono da sole: sul PC compare *Receiving “…” 45%*, sul telefono *Sending …*. Alla fine la scansione è nella lista del PC e sul telefono ha la spunta verde.
- ☐ **E3** Ripresa: stacca il cavo a metà trasferimento, ricollegalo: riparte dai file mancanti, non da zero.
- ☐ **E4** Cancella sul PC una scansione già ricevuta e ricollega il cavo: **non** deve tornare (il telefono sa che è già stata inviata).

**Via Wi-Fi (dopo A2)**
- ☐ **E5** Stessa Wi-Fi: barra verde **Connected by Wi-Fi · nome PC**. Con *Send new scans automatically* attivo, una scansione nuova parte da sola appena premi **Done**; altrimenti **Send** sulla riga.
- ☐ **E6** Se c'è un errore, sotto la scansione compare il motivo in rosso (es. il PC non risponde → firewall).

**Pulizia del telefono**
- ☐ **E7** In alto a destra c'è solo la rotellina **Impostazioni**.
- ☐ **E8** **Select** in alto a sinistra → spunta alcune scansioni → **Delete (n)**. Se tra queste ce n'è una non ancora inviata, l'avviso lo dice. Funziona anche lo swipe verso sinistra su una riga.

## F. Elaborazione e precisione (il test che conta)

- ☐ **F1** **Draft**: la forma c'è, niente pavimento, niente pezzi volanti.
- ☐ **F2** **High**: finisce senza errori. *Photos aligned* almeno il 90% delle foto. **Scale from: Scan mat**.
- ☐ **F3** Confronta col calibro e compila:

| Quota | Calibro (mm) | Calco (mm) | Differenza |
|---|---|---|---|
| Lunghezza X | | | |
| Larghezza Y | | | |
| Altezza Z | | | |

- ☐ **F4** La differenza è dentro il valore **Expected error** mostrato dall'app. Obiettivo per pezzi a incastro: ≤ 0,3 mm su oggetti di 10 cm in **High**.
- ☐ **F5** Stampa 3D di verifica: esporta **3D print**, stampa, misura il pezzo stampato.
- ☐ **F6** Stessa scansione **senza tappetino** (oggetto sul tavolo): **Scale from: LiDAR**, errore atteso ~1%. Serve a sapere quanto fidarti quando il tappetino non si può usare.

## G. Casi difficili (uno alla volta, annota il risultato)

| Caso | Come | Esito / note |
|---|---|---|
| ☐ Oggetto piccolo (2–5 cm) | **New scan → Macro**, pezzo sul tappetino **A5**, giri attorno a 5–10 cm: deve comparire *Macro lens*. 40–80 foto. Elabora **Medium**: la scala viene dal tappetino | |
| ☐ Oggetto ~1 m (es. il tappo) | Senza tappetino o con più fogli attorno; box grande; 100–150 foto | |
| ☐ Superficie lucida o nera | Prima senza niente, poi con spray opacizzante o pallini adesivi | |
| ☐ Superficie liscia e uniforme | Pallini adesivi sparsi a caso | |
| ☐ Pezzo di motore / metallo sporco | Luce diffusa, niente flash | |
| ☐ Busto / persona seduta | Modalità **Person**, occhi chiusi, 2 giri, ~60 s | |
| ☐ Esterno al sole | Preferisci ombra o cielo coperto; controlla le ombre nette nella texture | |
| ☐ Poca luce | Deve comparire *Too dark* | |
| ☐ Spazio / macchinario grande | Modalità **Space**, cammina lento | |

## H. Sopralluogo: lista da portarsi dietro

**Prima di partire**
- ☐ iPhone carico + powerbank; almeno 5 GB liberi (una scansione da 100 foto pesa ~400 MB).
- ☐ App Calco aperta una volta a casa (la firma di Sideloadly scade dopo 7 giorni: rinnovala prima).
- ☐ Tappetino rigido A4 (+A3), spray opacizzante, pallini adesivi, calibro, panno.

**Sul posto**
- ☐ Luce: diffusa e uniforme. Evita sole diretto e controluce.
- ☐ L'oggetto **non si muove** tra una foto e l'altra (niente piatto girevole: giri tu).
- ☐ **Place box**, poi due giri: uno basso, uno alto. Sovrapposizione abbondante, movimenti lenti.
- ☐ Dettagli importanti (fori, incastri, targhette): avvicinati e fai foto extra col pulsante fotocamera a sinistra.
- ☐ Se il pezzo va scansionato anche sotto: fai **due scansioni separate** (dritto e capovolto). *L'unione automatica non c'è ancora.*
- ☐ Misura col calibro **una quota di riferimento** e annotala: serve per la calibrazione se la scala non convince.
- ☐ **Prima di andare via**: anello di copertura quasi tutto verde, almeno 40 foto, e **fai una seconda scansione di riserva**. Sul telefono non c'è ancora l'anteprima 3D: l'unica verifica vera è al PC.

**Al rientro**
- ☐ **Send** di tutte le scansioni, **Draft** su tutte per vedere che siano buone, poi **High**/**Ultra** su quelle che servono (anche in coda durante la notte).

---

## Cosa non c'è ancora (non cercarlo durante il collaudo)

Anteprima 3D sul telefono · unione di due scansioni (sopra/sotto) · maschere AI per lo scontorno · Gaussian Splatting · import di foto/video esterni · export USDZ/FBX/STEP e sezioni DXF · report PDF del sopralluogo · finestra nativa Tauri (per ora l'interfaccia gira nel browser).


## Nota sui risultati

Nella scheda **Result** sul PC: **Blurred, skipped** = foto mosse scartate da sole; **Phone vs photos** = quanto le posizioni del telefono e quelle ricalcolate dalle foto concordano (bene sotto i 5 mm). Per i pezzi da stampare togli **Colour texture** in Process: stessa geometria, qualche minuto in meno.
