Eseguibile Windows (64 bit) di MagnetScout: un solo file, nessuna installazione,
nessuna dipendenza esterna.

## Avvio

1. Scarica `MagnetScout.exe` e mettilo in una cartella scrivibile
   (Desktop o Documenti).
2. Doppio clic. Il browser si apre su `http://127.0.0.1:8765/`.
3. Per fermarlo chiudi la finestra del terminale.

Al primo avvio Windows SmartScreen può chiedere conferma perché il file non è
firmato: *Ulteriori informazioni* → *Esegui comunque*. La build è prodotta
automaticamente da questo repository tramite GitHub Actions; il codice
sorgente è pubblico e puoi ricompilare l'eseguibile da solo con
`pyinstaller --clean --noconfirm magnetscout.spec`.

## Integrità

Verifica il file scaricato confrontando l'hash:

```powershell
(Get-FileHash .\MagnetScout.exe -Algorithm SHA256).Hash.ToLower()
Get-Content .\MagnetScout.exe.sha256
```

## Dati dell'utente

Preferenze, fonti personalizzate, coda XDCC e file scaricati restano **accanto
all'eseguibile**. Per ripartire da zero basta eliminare quei file.

Tutto gira su `127.0.0.1`: l'interfaccia non è raggiungibile dalla rete.

## Avvertenza legale

MagnetScout è un progetto **didattico**: non ospita contenuti, non è affiliato
ad alcun sito o titolare di diritti e non fornisce garanzie. Sei tu l'unico
responsabile di ciò che cerchi e scarichi. Testo completo:
[DISCLAIMER.md](https://github.com/NoShellButCloud/magnetscout/blob/main/DISCLAIMER.md).
