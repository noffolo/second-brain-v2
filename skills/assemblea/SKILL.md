---
name: assemblea
description: "Orchestra una simulazione di dibattito multipartitico (assemblea) tra 8 agenti con orientamenti politici, ideologici e culturali differenti. Viene utilizzata per analizzare un ordine del giorno (ODG) complesso, mettere in crisi le posizioni egemoniche (governo e grandi sindacati confederali) e ricavare una sintesi strategico-comunicativa per USB."
triggers: assemblea, simula dibattito, dibattito politico, ordine del giorno, odg, simulazione assemblea
argument-hint: "<ordine-del-giorno>" [--output-dir <path>]
---

# Skill Assemblea AI

Questa skill simula un dibattito tra 8 delegati ideologici per analizzare criticamente un tema e ricavare la migliore strategia comunicativa per USB, anticipando le controargomentazioni del governo e le risposte dei sindacati confederali.

## Partecipanti all'Assemblea
1. **Filo-governativo**: Difende il pareggio di bilancio, la stabilità macroeconomica e le riforme dell'esecutivo.
2. **Anarchico**: Rifiuta lo Stato, le tasse e la burocrazia sindacale; propone autogestione e azione diretta.
3. **Comunista**: Analizza il conflitto capitale-lavoro, propone la tassazione dei profitti e critica la subalternità dei partiti.
4. **Sindacato USB**: Sindacalismo di base e conflittuale; rivendica salario minimo, pensione a 62 anni, e opposizione ai contratti al ribasso.
5. **Sindacato CGIL**: Sindacalismo confederale; critica il governo ma difende il proprio ruolo negoziale e i compromessi contrattuali.
6. **Partito Democratico**: Centro-sinistra istituzionale; propone correzioni parlamentari e riforme nell'alveo della governance europea.
7. **Rappresentante dell'Astensionismo (Non-votante)**: Esprime lo scoramento e il disincanto della maggioranza invisibile che non crede più alla politica o ai sindacati.
8. **Confindustria**: Rappresenta gli industriali; chiede tagli a IRES/IRAP, flessibilità lavorativa e legame salario-produttività.

## Modalità d'Uso

### Da CLI (con lo script Python)
Puoi avviare una simulazione da terminale usando il Python del virtual environment:

```bash
.venv/bin/python skills/assemblea/scripts/run_assemblea.py "Discussione sull'ordine del giorno"
```

Lo script genererà una sintesi del dibattito e salverà la trascrizione completa all'interno di `wiki/synthesis/`.
