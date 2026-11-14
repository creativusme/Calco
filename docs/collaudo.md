# Calco — foglio di collaudo

Da fare **prima** del primo sopralluogo vero. Segui l'ordine: ogni blocco dà per buono il precedente. Se un punto fallisce, annota cosa è successo (e il testo del pulsante **Log** nell'app desktop) e fermati lì.

Legenda: ☐ da fare · scrivi a fianco OK / KO e una nota.

---

## A. Computer (una volta sola, ~15 min)

- ☐ **A1** Doppio clic su `Start Calco.cmd`. Si apre il browser su `http://localhost:7878` e in basso a sinistra compare **Engine ready** con la scheda video (RTX 3060).
- ☐ **A2** Windows chiede il permesso del firewall per Python: spunta **Reti private** e consenti. *Senza questo il telefono non vede il PC.*
- ☐ **A3** Clic su **Create a demo capture**. Dopo circa un minuto compare "Synthetic box + cylinder" nella lista.
- ☐ **A4** Qualità **Draft** → **Process**. Finisce in meno di un minuto; il modello ruota col mouse; le misure sono circa 85 × 63 × 83 mm (l'oggetto vero è 80 × 60 × 80: il LiDAR sbaglia di qualche mm, è normale).
- ☐ **A5** Qualità **Medium** → se richiesto **Install OpenMVS**, poi **Process**. Finisce in 3–5 minuti; misure entro ±1 mm da 80 × 60 × 80; **Scale from: Scan mat**.
- ☐ **A6** Viste **Texture / Clay / Wire** e **Grid** funzionano; tema chiaro/scuro (icona in alto a sinistra) funziona.
- ☐ **A7** Export **3D print** → **Export** → **Open export folder**. Apri lo STL nello slicer: dimensioni in mm corrette, base piatta appoggiata al piano, nessun errore di mesh aperta.
- ☐ **A8** Export **Blender / render** → apri il GLB in Blender: texture visibile, oggetto dritto.
- ☐ **A9** Calibrazione: in **Accuracy** scegli l'asse X, scrivi `100`, **Apply**. Le misure cambiano in proporzione; **Reset calibration** le riporta com'erano.

## B. Tappetino di scansione (una volta sola)

- ☐ **B1** Scarica **Scan mat A4** dalla schermata iniziale e stampalo al **100%** ("dimensioni reali", mai "adatta alla pagina").
- ☐ **B2** Misura col calibro la barra in basso: deve essere **100,0 mm**. Valore misurato: `______ mm`. Se non è 100,0 scrivilo in *Show advanced → Mat calibration bar* ogni volta che elabori (oppure ristampa).
- ☐ **B3** Incolla il foglio su un supporto rigido e piatto (cartone pressato, plexiglass). Un foglio ondulato sbaglia la scala.
- ☐ **B4** (facoltativo) Stampa anche l'A3 per oggetti fino a ~25 cm.

## C. iPhone: installazione

- ☐ **C1** Su GitHub la build **iOS app** è verde e produce `Calco-unsigned.ipa`.
- ☐ **C2** Installa con Sideloadly. L'app si apre sulla lista vuota "No scans yet".
- ☐ **C3** **New scan → Object**: consenti la **fotocamera**. Si vede l'immagine dal vivo con la griglia bianca del LiDAR che copre le superfici.
- ☐ **C4** Rotellina **Settings**: consenti la **rete locale**. Con Calco aperto sul PC e la stessa Wi-Fi, il PC compare in *Computers found*.
- ☐ **C5** Se non compare: scrivi a mano l'indirizzo mostrato in basso a sinistra sul PC (es. `192.168.1.20:7878`).

## D. Prima scansione di prova (in casa, oggetto facile)

Oggetto consigliato: una scatola opaca e non lucida, 10–20 cm, di cui conosci le misure col calibro. Buona luce diffusa, niente sole diretto.

- ☐ **D1** Oggetto al centro del tappetino. **New scan → Object**.
- ☐ **D2** Inquadra l'oggetto, **Place box**: compare un cubo arancione attorno all'oggetto. Regola la dimensione col cursore finché lo contiene tutto con un po' di margine.
- ☐ **D3** Tocca il pulsante rosso: parte lo scatto automatico. Il contatore foto sale mentre ti muovi; **non** scatta se stai fermo.
- ☐ **D4** Muoviti troppo in fretta: compare **Slow down** e smette di scattare. Rallenta: riprende.
- ☐ **D5** Fai un giro completo ad altezza oggetto, poi un secondo giro dall'alto (~45°). L'anello di copertura in alto si riempie di verde (anello interno = giro basso, esterno = giro alto).
- ☐ **D6** Puntini bianchi restano sospesi nei punti da cui hai scattato.
- ☐ **D7** **Done** con 40–80 foto. La scansione compare nella lista con anteprima e peso in MB.
- ☐ **D8** Prova anche: chiudi con la **X** a metà scansione → chiede conferma → **Discard** cancella tutto.

## E. Invio al computer

- ☐ **E1** **Send**: la percentuale sale, alla fine spunta verde. Sul PC la scansione compare da sola in lista entro un paio di secondi.
- ☐ **E2** Ripresa: avvia un **Send**, a metà spegni il Wi-Fi del telefono (errore rosso), riaccendi, **Send** di nuovo: riparte da dove era arrivato, non da zero.
- ☐ **E3** Riserva senza rete: collega l'iPhone al PC, app **File**/iTunes → Calco → copia la cartella `….calco` in `C:\Users\User\Calco\captures\`. Compare in lista.

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
| ☐ Oggetto piccolo (2–5 cm) | Tappetino, avvicinati il più possibile restando a fuoco (~15 cm), 60+ foto | |
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

Anteprima 3D sul telefono · modalità Macro per pezzi sotto i 3 cm · unione di due scansioni (sopra/sotto) · maschere AI per lo scontorno · Gaussian Splatting · import di foto/video esterni · export USDZ/FBX/STEP e sezioni DXF · report PDF del sopralluogo · finestra nativa Tauri (per ora l'interfaccia gira nel browser).
