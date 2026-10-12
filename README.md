# DOINg.AI Orchestrator

**Un agent de code façon [OpenCode](https://opencode.ai), en React + Python, branché sur votre LLM local (le même que NEXUS.AI), avec les mods [savvy-flow](https://github.com/JohnnyVizz/claude-kit/tree/main/plugins/savvy-flow) et [savvy-progress](https://github.com/JohnnyVizz/claude-kit/tree/main/plugins/savvy-progress).**

- **Agents OpenCode** : `Build` (lit, modifie, exécute), `Plan` (lecture seule) ; `Tab` pour changer d'agent.
- **Outils** : `read`, `list`, `glob`, `grep`, `write`, `edit` (avec diff), `bash`, `todowrite`, `task` (délégation à un worker).
- **savvy-flow** (`/flow <tâche>`) : l'orchestrateur planifie, possède le design, délègue à des workers par niveau (`fable` / `heavy` / `careful` / `medium` / `light`), puis fait une **revue adversariale** de chaque rendu (accepte ou renvoie avec la liste des défauts, au même niveau ou au-dessus, 2 tours max).
- **savvy-progress** : barre de progression au-dessus du prompt (titre, phase, tâches acceptées / planifiées, segments par tâche) et **panneau Agents** (en cours, terminés, planifiés ; modèle, effort, étapes auto-déclarées, contexte, tokens, durée, rapport et transcript de chaque worker). L'équipe est animée : l'astronaute flotte, le détective balaie sa loupe, l'ingénieur tourne sa clé, le chef fait sauter l'omelette, le pilote file (désactivé si `prefers-reduced-motion`).
- **100 % local** : aucun appel externe, aucun suivi. Fournisseurs identiques à NEXUS.AI : **Ollama**, **OpenAI-compatible** (LM Studio, vLLM, llama.cpp, LocalAI…), **webhook n8n**.

---

## Démarrage rapide

Prérequis : **Node.js 20+** et **Python 3.10+**.

**Windows**

```cmd
install.bat
start.bat
```

**macOS / Linux**

```bash
./install.sh
./start.sh            # http://localhost:4001
```

Développement (hot reload) : `start-dev.bat`, ou `python server.py` + `cd frontend && npm run dev` → <http://localhost:4000>.

Les ports 4000/4001 ne gênent pas NEXUS.AI (3000/3001) : les deux applis tournent côte à côte.

## Brancher votre LLM local (NEXUS.AI)

Trois façons, au choix :

1. **Automatique** : au premier lancement, si un dossier `NEXUS.AI` (ou `nexus.ai`) est à côté de ce projet, son `llmConfig` (fournisseur, URL, clé, modèle) est importé tout seul depuis son `db.json` (en suivant `server-config.json` si la base a été déplacée).
2. **Réglages → LLM local → Importer depuis NEXUS.AI** : donnez le chemin du dossier NEXUS.AI ou de son `db.json`.
3. **À la main** dans Réglages → LLM local, puis **Tester la connexion** (liste les modèles ; cliquez pour en choisir un).

| Fournisseur | URL d'exemple | Remarques |
| --- | --- | --- |
| Ollama | `http://localhost:11434` | `ollama pull qwen2.5-coder:14b` |
| OpenAI-compatible | `http://localhost:1234` (LM Studio), `http://localhost:8000` (vLLM) | clé Bearer optionnelle |
| n8n | `https://votre-n8n/webhook/...` | reçoit `{prompt, messages, model}`, renvoie du texte |

**Appel d'outils.** En mode `auto`, l'appli utilise l'appel d'outils natif et bascule toute seule sur un protocole texte (`<tool_call>{json}</tool_call>`, format Hermes / Qwen) si le serveur ou le modèle refuse les outils. n8n utilise toujours le protocole texte. Les blocs `<think>` des modèles de raisonnement sont affichés à part, repliés.

**Modèles conseillés.** Un modèle « coder » qui sait appeler des outils (Qwen2.5-Coder, Qwen3, Devstral, Llama 3.1+…). Donnez le plus fort à l'**orchestrateur** (Réglages → Niveaux savvy) et des modèles plus légers aux niveaux `light` / `medium`. Augmentez la **fenêtre de contexte** si votre serveur le permet (envoyée à Ollama en `num_ctx`).

## Utilisation

| Touche / commande | Effet |
| --- | --- |
| `Entrée` / `Maj+Entrée` | envoyer / nouvelle ligne |
| `Tab` | Build → Plan → Savvy Flow |
| `Échap` | interrompre |
| `@chemin` | joindre un fichier (autocomplétion) |
| `/flow <tâche>` | lancer savvy-flow |
| `/new`, `/build`, `/plan`, `/agents`, `/settings`, `/init`, `/help` | — |

`/init` demande à l'agent build de créer ou mettre à jour `AGENTS.md` ; ce fichier (ou `CLAUDE.md`) est ensuite injecté dans le prompt de tous les agents.

**Autorisations.** Par défaut les modifications de fichiers sont autorisées et les commandes shell demandent confirmation (*Autoriser une fois* / *Toujours pour cette session* / *Refuser*). Les agents ne peuvent pas sortir du dossier de la session.

### Le déroulé savvy-flow

```
 /flow <tâche>
     │
     ▼
 PLAN ── l'orchestrateur lit le projet (outils lecture seule) et rend un plan JSON :
     │   tâches (titre, niveau, dépendances, brief autonome, vérification), specs de design,
     │   questions éventuelles, SPEC si projet vierge
     ▼
 [votre validation]  ← ou répondez dans le prompt pour faire réviser le plan
     │
     ▼
 DESIGN (si l'UI est touchée : les specs sont injectées dans chaque brief, les workers n'inventent rien)
     │
     ▼
 DELEGATE ── workers par niveau, dans l'ordre des dépendances (N en parallèle, réglable)
     │          chaque worker déclare ses étapes (outil `step`) → panneau Agents
     ▼
 REVIEW ── l'orchestrateur relit le diff réel, relance la vérif si besoin
     │       accept → tâche acceptée (+ commit git optionnel)
     │       return → renvoyée avec les défauts, même niveau ou plus haut (2 tours max)
     ▼
 CLOSE ── synthèse : ce qui a changé, ce qui est vérifié, ce qui reste
```

Projet vierge (dossier vide) : l'orchestrateur écrit aussi une SPEC (`docs/SPEC.md`), la tâche 1 est toujours le **scaffold** (avec `AGENTS.md` et `filemap.md`), et `docs/PLAN.md` est tenu à jour à chaque tâche acceptée.

Contrairement au skill d'origine où le modèle enchaîne tout lui-même, ici **le séquencement est piloté par le serveur Python** et le LLM joue chaque rôle (planificateur, worker, relecteur). C'est volontaire : un modèle local de 7 à 30B tient mal une orchestration longue, alors qu'il s'en sort bien sur des étapes courtes au format de sortie strict. La barre de progression reste juste même si le modèle oublie de se déclarer.

| Niveau | Effort par défaut | Budget d'outils | Pour |
| --- | --- | --- | --- |
| 🧑‍🚀 fable | high | 30 | logique et bugs les plus durs (réserve) |
| 🕵️ heavy | xhigh | 40 | code inconnu, cause floue |
| 🧑‍🔧 careful | high | 30 | changements délicats, invariants |
| 🧑‍🍳 medium | medium | 20 | fonctionnalités standard |
| 🏎️ light | low | 12 | éditions mécaniques, builds, tests |

## Essayer sans GPU

Un faux LLM scripté joue tous les rôles (Ollama + OpenAI-compatible, avec streaming) :

```bash
python scripts/fake_llm.py --port 11500            # outils natifs
python scripts/fake_llm.py --port 11500 --no-tools # force le protocole texte
```

Puis Réglages → LLM local → Ollama, `http://localhost:11500`, modèle `fake-coder:7b`.

## Architecture

```
 Navigateur ── React 19 · Vite · Tailwind ─────────────────────────────────┐
 │ Sessions │ Transcript (texte, outils, diffs, plan, revues) │ Panneau     │
 │          │ Barre savvy-progress · Autorisations · Prompt    │ Agents      │
 └──────────────── REST /api/*  +  SSE /api/sessions/{id}/events ──────────┘
                                   │
 FastAPI (server.py → backend/) ───┴───────────────────────────────────────┐
 │ agent.py   boucle agent : LLM ⇄ outils, autorisations, budget d'étapes  │
 │ flow.py    moteur savvy-flow : plan → design → delegate → review → close│
 │ tools.py   read/list/glob/grep/write/edit/bash/todo/step/task (sandbox) │
 │ llm.py     Ollama · OpenAI-compatible · n8n, streaming, outils natifs   │
 │            ou protocole texte, séparation <think>                       │
 │ session.py sessions, runs, bus d'événements, persistance data/          │
 └──────────────────────────────────┬────────────────────────────────────────┘
                                    ▼
                     votre LLM local (comme NEXUS.AI)
```

Les données (réglages, sessions) sont dans `data/` (`ORCH_DATA_DIR` pour changer). Voir [`filemap.md`](filemap.md) pour le détail des fichiers.

## Tests

```bash
pip install -r requirements.txt
python -m pytest            # backend : outils, protocole texte, autorisations, savvy-flow complet
cd frontend && npm run lint # typage TypeScript
```

## Crédits

- Interface et agents inspirés d'[OpenCode](https://github.com/sst/opencode).
- savvy-flow et savvy-progress : [JohnnyVizz/claude-kit](https://github.com/JohnnyVizz/claude-kit) (MIT), adaptés aux LLM locaux.
- Configuration LLM reprise de NEXUS.AI / DOINg.AI.
