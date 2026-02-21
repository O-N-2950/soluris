# 🗂️ CONTEXT.md — Soluris (soluris.ch)
> Dernière mise à jour : 21 février 2026
> Coller en début de chaque conversation dans le projet "Soluris"

---

## 🎯 VUE D'ENSEMBLE

**Soluris** = Assistant juridique IA spécialisé en droit suisse. Répond aux questions juridiques en citant les sources exactes (Fedlex, jurisprudence ATF, Tribunal fédéral, cantons romands).

- **URL :** https://soluris.ch
- **Repo :** https://github.com/O-N-2950/soluris
- **Hébergement :** Railway (Docker)
- **© 2026 Soluris, Genève, Suisse**

---

## 🏗️ STACK TECHNIQUE

| Couche | Tech |
|--------|------|
| Backend | Python 3.11 + FastAPI (async) |
| Frontend | HTML/CSS/JS vanilla — design dark-mode premium |
| BDD | PostgreSQL + pgvector (embeddings pour RAG) |
| IA | Claude API (Anthropic) — `claude-sonnet-4-20250514` |
| Sources légales | Fedlex (SPARQL), Entscheidsuche, Tribunal fédéral, cantons romands |
| Deploy | Railway + Docker |

---

## 📁 STRUCTURE DU PROJET

```
soluris/
├── backend/
│   ├── main.py                    # FastAPI entrypoint
│   ├── db/
│   │   └── database.py            # Init PostgreSQL + pgvector
│   ├── models/                    # SQLAlchemy models
│   ├── routers/
│   │   ├── auth.py                # JWT auth
│   │   ├── chat.py                # /api/chat — endpoint principal
│   │   ├── conversations.py       # Historique conversations
│   │   └── health.py              # Health check
│   ├── services/
│   │   └── rag.py                 # Pipeline RAG + appel Claude API
│   └── scrapers/
│       ├── fedlex.py              # Fedlex SPARQL (lois fédérales)
│       └── entscheidsuche.py      # Jurisprudence
├── frontend/
│   ├── index.html                 # Landing page
│   ├── app.html                   # Application chat
│   ├── login.html                 # Connexion
│   ├── css/styles.css
│   └── js/
│       ├── app.js                 # Logic chat
│       ├── auth.js                # Auth frontend
│       └── landing.js
├── Dockerfile
├── railway.toml
└── requirements.txt
```

---

## 🧠 FONCTIONNEMENT RAG

### Flux actuel (en cours de développement)
```
Question utilisateur
→ [TODO] Embedding question (Cohere multilingual)
→ [TODO] Recherche vectorielle pgvector sur legal_chunks
→ Contexte légal injecté dans system prompt
→ Claude Sonnet génère la réponse avec citations
→ Sources parsées depuis bloc [SOURCES]...[/SOURCES]
→ Réponse structurée + sources JSON
```

**Note :** Le RAG vectoriel est **TODO** — actuellement Claude répond sur sa connaissance native du droit suisse.

### System Prompt (règles strictes de Soluris)
1. Répond UNIQUEMENT sur le droit suisse (fédéral + cantonal)
2. Cite TOUJOURS les sources exactes (articles, ATF)
3. Indique clairement l'incertitude
4. Ne donne JAMAIS de conseil juridique personnel — information juridique uniquement
5. Répond en français par défaut
6. Structure claire avec références entre parenthèses
7. Jurisprudence mentionnée quand pertinente

### Format sources (parsé automatiquement)
```
[SOURCES]
[{"reference": "Art. 41 CO", "title": "Responsabilité délictuelle", "url": "https://www.fedlex.admin.ch/..."}]
[/SOURCES]
```

---

## 📡 API ROUTES

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Inscription |
| POST | `/api/auth/login` | Connexion JWT |
| POST | `/api/chat` | Question juridique → réponse IA |
| GET | `/api/conversations` | Historique conversations |
| GET | `/api/health` | Health check |
| GET | `/` | Landing page |
| GET | `/app` | Interface chat |
| GET | `/login` | Page connexion |

---

## 📚 SOURCES LÉGALES INTÉGRÉES

- **Fedlex** : Législation fédérale suisse via SPARQL endpoint (`fedlex.data.admin.ch`)
  - Requêtes SPARQL en français, lois avec numéro RS
- **Entscheidsuche** : Jurisprudence (`entscheidsuche.ch`)
- **Tribunal fédéral** : ATF (Arrêts du Tribunal Fédéral)
- **Cantons romands** : Jurisprudence cantonale

---

## ⚠️ POINTS D'ATTENTION

1. **Claude = seul provider IA** — `claude-sonnet-4-20250514` via API Anthropic directe (httpx, pas de SDK)
2. **pgvector** — Extension PostgreSQL requise pour embeddings — vérifier que Railway la supporte
3. **RAG TODO** — L'embedding vectoriel n'est pas encore implémenté, Claude répond sur sa connaissance
4. **Pas de conseil juridique** — Règle fondamentale du product — information uniquement, jamais de conseil personnalisé
5. **Historique limité** — 8 derniers messages passés à Claude pour le contexte conversationnel
6. **Tokens trackés** — Chaque réponse retourne le nombre de tokens utilisés

---

## 🔑 VARIABLES D'ENVIRONNEMENT

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL + pgvector (Railway) |
| `ANTHROPIC_API_KEY` | Clé API Anthropic |
| `ANTHROPIC_MODEL` | Modèle Claude (défaut: `claude-sonnet-4-20250514`) |
| `JWT_SECRET` | Secret tokens JWT |

---

## 🔗 LIENS UTILES

- Site : https://soluris.ch
- Repo : https://github.com/O-N-2950/soluris
- Fedlex SPARQL : https://fedlex.data.admin.ch/sparqlendpoint
- Entscheidsuche : https://www.entscheidsuche.ch
