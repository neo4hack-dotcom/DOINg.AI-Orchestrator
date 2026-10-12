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
| `Entrée` / `Maj+Entrée` | envoyer / nouvelle ligne (pendant que l'agent travaille, le message part en **file d'attente**) |
| `Tab` | Build → Plan → Savvy Flow |
| `↑` / `↓` | historique des prompts (brouillon conservé par session) |
| `Échap` | interrompre |
| `@chemin` | joindre un fichier (autocomplétion) |
| `Ctrl+K` · `Alt+N` · `Ctrl+B` · `Ctrl+.` | palette · nouvelle session · barre latérale · panneau latéral |
| `/flow <tâche>` | lancer savvy-flow |
| `/goal <condition>` | mode objectif : l'agent continue jusqu'à ce qu'un évaluateur confirme la condition |
| `/undo` | annuler le dernier tour (fichiers **et** conversation, via snapshot git) |
| `/compact` · `/fork` · `/report` · `/auto` | résumer le contexte · bifurquer la session · télécharger le rapport · mode autonome |
| `/new`, `/build`, `/plan`, `/agents`, `/settings`, `/init`, `/help` | — |

`/init` crée ou met à jour `AGENTS.md` ; ce fichier (ou `CLAUDE.md`) est injecté dans le prompt de tous les agents.

**Autorisations.** Modifications de fichiers autorisées, shell sur demande (*une fois* / *toujours* — mémorisé par motif de commande, ex. `npm test*` / *refuser*). Des **règles shell** (`git status* = allow`, `git push* = deny`, `sudo * = deny`…) s'appliquent sous-commande par sous-commande (`&&`, `;`, `|`). Le **mode autonome** accorde tout ce qui serait demandé (les refus restent) : à activer pour les longues sessions sans surveillance. Valider un plan pré-autorise ses commandes de vérification. Les agents ne peuvent pas sortir du dossier de la session.

## Harnais : ce qui le rend fiable sur 10-15 h

Inspiré du code d'[OpenCode](https://github.com/sst/opencode) et d'[OpenClaude](https://github.com/Twigpine/openclaude) :

| # | Amélioration | Effet |
| --- | --- | --- |
| 1 | `edit` tolérant (exact → guillemets/tirets normalisés → espaces de fin → indentation ; préfixes de numéros de ligne retirés ; CRLF conservé) | bien moins d'échecs d'édition avec les modèles locaux |
| 2 | `multiedit` atomique | plusieurs changements d'un fichier en un appel, tout ou rien |
| 3 | `apply_patch` (format OpenCode) | créer / modifier / déplacer / supprimer plusieurs fichiers en un appel |
| 4 | lecture obligatoire avant édition + détection de fichier modifié depuis la lecture | aucun worker n'écrase le travail d'un autre en parallèle |
| 5 | verrous par fichier | écritures concurrentes sérialisées |
| 6 | sorties longues coupées + fichier complet relisible | moins de tokens, rien de perdu |
| 7 | élagage des vieilles sorties d'outils | contexte stable sur des centaines d'appels |
| 8 | compaction en point de contrôle structuré (objectif, décisions, état, prochaine étape, fichiers) — le brief reste épinglé | les longues tâches ne débordent jamais du contexte |
| 9 | reprise sur dépassement de contexte | compaction forcée puis nouvel essai |
| 10 | retries avec backoff exponentiel + jitter (connexion, 5xx, 429, timeouts) | un serveur LLM qui hoquette ne tue pas la nuit |
| 11 | garde-fou anti-boucle (appels identiques répétés) + relance après échecs en série | pas de tokens brûlés en boucle |
| 12 | budget d'étapes avec rapport final forcé | chaque worker finit proprement |
| 13 | snapshots git fantômes (`/undo`, fichiers modifiés, diff, restauration par fichier) | tout est réversible, sans toucher à votre `.git` |
| 14 | reprise automatique des flows après crash / redémarrage | une coupure à 3 h du matin ne perd pas la nuit |
| 15 | règles de permission par motif + « toujours » par commande + mode autonome | aucune session bloquée sur une question |
| 16 | outil `repomap` (fichiers les plus référencés + signatures) injecté au planificateur | moins d'exploration, meilleurs plans |
| 17 | outils `question`, `webfetch`, `notes` (contrats partagés entre workers) | décisions explicites, docs à portée, cohérence en parallèle |
| 18 | mode objectif (`/goal`) avec évaluateur | travail continu jusqu'à un critère vérifiable |
| 19 | auto-vérification + auto-correction avant la revue, escalade de niveau, timeout par tâche | moins de tours de revue, pas de tâche bloquée |
| 20 | journal JSONL + rapport Markdown + `docs/PLAN.md` / `docs/FLOW_REPORT.md` + commits git tracés | traçabilité complète de ce qui a été fait, par qui, avec quel résultat |

Plus : fork de session, compaction manuelle, prompts système à préfixe stable (cache KV des serveurs locaux).

### Découpage du plan initial

Pour une grosse demande (une app entière), l'orchestrateur procède en deux temps :

1. **Architecte** : il lit le projet (repo map) et découpe en **modules homogènes** (une couche ou une fonctionnalité chacun : modèle de données, API, auth, pages, tests…), chacun avec ses fichiers, ses **contrats** (signatures, routes, types exposés aux autres modules) et ses dépendances. Petite demande : liste de tâches directe.
2. **Un appel JSON court par module** produit 2 à 8 tâches de taille comparable, avec taille (S/M/L), complexité (1-5), fichiers possédés, dépendances minimales, brief autonome et commande de vérification.

Puis le planificateur (`backend/planner.py`) :
- **découpe** les tâches trop grosses (L et complexité ≥ 4, brief énorme, trop de fichiers) en 2-4 sous-tâches ;
- **fusionne** les chaînes de micro-tâches (moins de démarrages de workers = moins de tokens) ;
- vérifie le **niveau** contre la complexité (une tâche de complexité 5 ne part jamais chez un worker light) ;
- n'autorise que des dépendances vers l'arrière (pas de cycle possible), relie les modules par leurs tâches de sortie ;
- **sérialise les conflits de fichiers** : deux tâches qui touchent les mêmes fichiers ne tournent jamais en même temps ;
- calcule **vagues** et **chemin critique** : l'ordonnanceur lance d'abord les chaînes les plus longues, en parallèle jusqu'à `maxParallel`.

Après chaque tâche : vérification automatique (le worker corrige lui-même ses échecs), revue adversariale (ou acceptation directe d'une tâche light dont la vérif passe), **porte de module** (la vérif du module quand toutes ses tâches sont acceptées), puis **phase d'intégration** : vérifications globales et, en cas d'échec, tâches de correction planifiées automatiquement.

### Interface (10 améliorations)

1. **Palette de commandes** (`Ctrl+K`) : commandes, panneaux, sessions.
2. **File d'attente** : on peut écrire pendant que l'agent travaille ; envoi automatique dès qu'il est libre.
3. **Historique des prompts** (`↑/↓`) et brouillon conservé par session.
4. **Panneau latéral à onglets** : *Agents* (savvy-progress), *Plan* (modules, vagues, chemin critique, vérifs, tokens et durée par tâche), *Fichiers*, *Trace*.
5. **Fichiers modifiés** : diff par fichier et restauration en un clic.
6. **Trace** : journal en direct filtrable (décisions, outils, erreurs) et export du rapport.
7. **Notifications** bureau + badge d'onglet (autorisation, question, fin de flow).
8. **Appels d'outils regroupés** au-delà de 3, bouton « Aller au plus récent », défilement qui ne saute plus.
9. **Barre d'état** : jauge de contexte, tokens, compactions, objectif, bascule *Autonome*.
10. **Actions sur les messages** : copier, relancer, modifier et renvoyer, bifurquer d'ici ; questions de l'agent avec choix cliquables.

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
 │ agent.py   boucle agent : LLM ⇄ outils, retries, compaction, anti-boucle│
 │ flow.py    savvy-flow : plan → design → delegate → review → verify     │
 │ planner.py découpage : modules, DAG, conflits, vagues, chemin critique  │
 │ tools.py   read/edit/multiedit/apply_patch/bash/repomap/... (sandbox)   │
 │ snapshot / journal / compaction / permissions / repomap / editing       │
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
python -m pytest            # 48 tests : édition, patch, permissions, planificateur, snapshots, compaction,
                            # retries, débordement, autofix, budget, reprise après crash, undo/fork, goal, flow modulaire
cd frontend && npm run lint # typage TypeScript
```

## Crédits

- Interface et agents inspirés d'[OpenCode](https://github.com/sst/opencode).
- savvy-flow et savvy-progress : [JohnnyVizz/claude-kit](https://github.com/JohnnyVizz/claude-kit) (MIT), adaptés aux LLM locaux.
- Configuration LLM reprise de NEXUS.AI / DOINg.AI.
