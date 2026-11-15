# Calco — foglio di collaudo

Restano solo le prove non ancora fatte (le voci già spuntate e verificate sono state tolte). Se un punto fallisce, annota cosa è successo (e il testo del pulsante **Log** nell'app desktop) e fermati lì.

Legenda: ☐ da fare · scrivi a fianco OK / KO e una nota.

---

## A. Novità da ricontrollare

- ☐ **A14** Wi-Fi bloccato da Windows: **How to fix** mostra ora il comando completo, con i percorsi interi, e il pulsante **Copy Command**. Incollalo in PowerShell aperto come amministratore: deve finire senza errori, poi **I Ran It · Check Again**.
- ☐ **A15** **Scan Mat → Markers PDF**: il testo della barra da 100 mm non si sovrappone più ai riquadri dei marker.
- ☐ **A16** La pagina GitHub del progetto ha il link **Download the latest iPhone app**: scarica l'ultima IPA senza passare da Actions.

## F. Elaborazione

- ☐ **F7** Due scansioni dello stesso pezzo (es. fronte e retro), entrambe elaborate. Nella scheda **Process** di una delle due: **Combine with another side…** → scegli l'altra → **Combine Sides**. Il risultato è un modello unico chiuso; in **Export** trovi anche i due lati allineati separati (`side1.ply`, `side2.ply`) per rifiniture in un altro programma.
- ☐ **F8** Controlla *Sides distance*: sotto ~1 mm i lati combaciano bene. Se l'app dice che si sovrappongono troppo poco, rifai le scansioni in modo che i bordi e i fianchi del pezzo si vedano in entrambe.
- ☐ **F17** Rielabora in **High** una scansione già fatta: nel **Log** compare *Part inside the crop box* e *Cropped N photos*. Il modello ha più dettaglio di prima, a parità di tempo.
- ☐ **F18** **Measure → Cylinder**: un clic sul fianco di un albero o dentro un foro; compaiono due cerchi e l'asse sul modello, e il diametro è entro ~0,3 mm dal calibro. **Measure → Plane** su una faccia piana: *Flatness* di pochi decimi di mm e *Tilt* vicino a 0° per una faccia orizzontale.
- ☐ **F19** **Export → Photos + Cameras (ZIP)**: dentro ci sono `images/`, `colmap/`, `transforms.json`, `xmp/`, `cameras.csv`. Prova in un altro programma, per esempio RealityScan (foto + file `.xmp` nella stessa cartella) o un programma di splatting (cartella con `transforms.json`): le fotocamere devono comparire già al loro posto. Senza elaborare: **Process → Only export the photos** (pose del telefono).
- ☐ **F20** Export **USDZ** (profilo *Web / AR*): mandalo all'iPhone (AirDrop o File) e aprilo: si vede in AR con le misure giuste.

## G. Casi difficili (uno alla volta, annota il risultato)

| Caso | Come | Esito / note |
|---|---|---|
| ☐ Oggetto piccolo (2–5 cm) | Scansione **Object** normale sul tappetino **A5**: prima un giro a ~25 cm, poi avvicinati: deve partire da solo *Close-up lens*. Elabora **Medium**: la scala viene dal tappetino | |
| ☐ Oggetto ~1 m (es. il tappo) | Con 3–4 marker sciolti attorno (**Markers PDF**); box grande; 100–150 foto | |
| ☐ Superficie lucida o nera | Prima senza niente, poi con spray opacizzante o pallini adesivi | |
| ☐ Superficie liscia e uniforme | Pallini adesivi sparsi a caso | |
| ☐ Griglia / reticolo a pareti sottili | Sotto il pezzo un fondo scuro e opaco (non il tappetino bianco), spray opacizzante, foto ravvicinate perpendicolari alla griglia oltre al giro normale. Pareti sotto ~2 mm restano al limite per la fotogrammetria da telefono | |
| ☐ Pezzo di motore / metallo sporco | Luce diffusa, niente flash | |
| ☐ Busto / persona seduta | Modalità **Person**, occhi chiusi, 2 giri, ~60 s | |
| ☐ Esterno al sole | Preferisci ombra o cielo coperto; controlla le ombre nette nella texture | |
| ☐ Poca luce | Deve comparire *Too dark* | |
| ☐ Spazio / macchinario grande | Modalità **Space**, cammina lento | |

---

## Promemoria per il sopralluogo (non si spunta: vale ogni volta)

**Prima di partire**
- iPhone carico + powerbank; almeno 5 GB liberi (una scansione da 100 foto pesa ~400 MB).
- App Calco aperta una volta a casa (la firma di Sideloadly scade dopo 7 giorni: rinnovala prima).
- Tappetino rigido A4 (+A3), marker sciolti per i pezzi grandi, spray opacizzante, pallini adesivi, calibro, panno.

**Sul posto**
- Luce diffusa e uniforme. Evita sole diretto e controluce.
- L'oggetto **non si muove** tra una foto e l'altra (niente piatto girevole: giri tu).
- **Place box**, poi due giri: uno basso, uno alto. Movimenti lenti.
- Dettagli importanti (fori, incastri, targhette): avvicinati, la lente ravvicinata parte da sola.
- Pezzo da scansionare anche sotto: **Flip** a metà scansione, oppure due scansioni separate da unire con **Combine**.
- Misura col calibro **una quota di riferimento** e annotala.
- Prima di andare via: copertura quasi tutta verde, almeno 40 foto, e una seconda scansione di riserva.

**Al rientro**
- **Send** di tutte le scansioni, **Draft** per vedere che siano buone, poi **High** su quelle che servono (anche in coda durante la notte).

## Cosa non c'è ancora

Maschere per scontornare oggetti che non poggiano su un piano · finestra nativa Tauri (per ora l'interfaccia gira nel browser).

## Nota sui risultati

Nella scheda **Result** sul PC: **Blurred, skipped** = foto mosse scartate da sole; **Phone vs photos** = quanto le posizioni del telefono e quelle ricalcolate dalle foto concordano (bene sotto i 5 mm). Per i pezzi da stampare togli **Colour texture** in Process: stessa geometria, qualche minuto in meno.
