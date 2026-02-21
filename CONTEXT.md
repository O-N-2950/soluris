# SOLURIS — Contexte Projet

> Fichier mis à jour automatiquement à chaque session. Sert de mémoire persistante entre les conversations.

## 🎯 Vision

**Soluris** = "Solutio" (solution) + "Iuris" (du droit). Plateforme d'intelligence juridique suisse propulsée par l'IA. Cible : avocats, études, magistrats en Suisse romande.

- **Domaine choisi** : `soluris.ch` (confirmé disponible via RDAP — pas encore enregistré)
- **Positionnement** : Premium, institutionnel, "la référence de confiance"
- **Pricing** : Solo CHF 149/mo, Cabinet CHF 449/mo, Enterprise sur mesure. Essai 14j gratuit sans CB.

## 🏗 Stack Technique

| Composant | Technologie |
|-----------|-------------|
| Frontend | HTML/CSS/JS vanilla (pas de framework) |
| Backend | FastAPI (Python 3.11) |
| Base de données | PostgreSQL + pgvector |
| Auth | JWT (python-jose, bcrypt, 72h expiration) |
| IA | Claude API (claude-sonnet-4-20250514) |
| Embeddings | Cohere multilingual (prévu, pas encore implémenté) |
| Déploiement | Railway (Dockerfile) |
| Repo | https://github.com/O-N-2950/soluris |

## 🎨 Design System (v2 — Premium Éditorial)

Redesign complet aligné sur le logo (hexagone réseau neuronal + point doré central).

**Palette :**
- Navy deep `#06101F` (fond principal)
- Navy `#0B1F3B` (cartes, surfaces)
- Or `#C6A75E` (CTA, accents — usage parcimonieux)
- Cream `#F5F0E8` (texte principal)
- Text secondary `#8A9AB5`

**Typographie :**
- Titres : Cormorant Garamond (serif, autorité institutionnelle)
- Corps : DM Sans (lisibilité moderne)
- Code : JetBrains Mono

**Esthétique :** Éditoriale, lignes fines dorées, espacement généreux, pas de glow excessif. "Banque privée genevoise" plutôt que "startup tech".

## 📁 Structure du Projet

```
soluris/
├── frontend/
│   ├── index.html          ← Landing page (premium editorial)
│   ├── app.html             ← Interface chat
│   ├── login.html           ← Auth (login/signup)
│   ├── css/styles.css       ← Design system complet (1450+ lignes)
│   ├── js/
│   │   ├── app.js           ← Chat + API integration
│   │   ├── auth.js          ← Login/signup logic
│   │   └── landing.js       ← Scroll animations
│   └── assets/
│       ├── logo-soluris.svg       ← Logo complet avec texte
│       ├── logo-icon-dark.svg     ← Hexagone seul (navbar, favicon)
│       ├── logo-soluris.png       ← PNG fond transparent (522x392)
│       ├── logo-soluris-md.png    ← PNG 80px height
│       └── logo-soluris-nav.png   ← PNG 40px height
├── backend/
│   ├── main.py              ← FastAPI entry, CORS, static serving
│   ├── db/database.py       ← asyncpg pool, schema init
│   ├── routers/
│   │   ├── auth.py          ← JWT login/signup/me
│   │   ├── chat.py          ← RAG endpoint /api/chat
│   │   ├── conversations.py ← History /api/conversations
│   │   └── health.py        ← /health check
│   ├── services/rag.py      ← Claude API + (TODO) vector retrieval
│   └── scrapers/
│       ├── fedlex.py        ← SPARQL endpoint
│       └── entscheidsuche.py ← Court decisions API
├── Dockerfile
├── railway.toml
├── requirements.txt
├── README.md
└── CONTEXT.md               ← Ce fichier
```

## 🗃 Base de Données (Schema)

- **users** : id (UUID), email, name, password_hash, plan, queries_this_month
- **conversations** : id (UUID), user_id → users, title, timestamps
- **messages** : id (SERIAL), conversation_id → conversations, role, content, sources (JSONB), tokens_used
- **legal_documents** : id, source, external_id, doc_type, title, reference, jurisdiction, language, content, publication_date, url, metadata (JSONB)
- **legal_chunks** : id, document_id → legal_documents, chunk_index, chunk_text, source_ref, source_url, embedding (BYTEA)

## 🔌 API Routes

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/auth/login` | Email/password → JWT |
| POST | `/api/auth/signup` | Création compte → JWT |
| GET | `/api/auth/me` | Info user depuis token |
| POST | `/api/chat` | Message + conversation_id → réponse IA |
| GET | `/api/conversations` | Liste conversations user |
| GET | `/api/conversations/{id}/messages` | Messages d'une conversation |
| GET | `/health` | Health check (DB status) |

## 📊 Sources de Données Juridiques

1. **Fedlex** (SPARQL) — Législation fédérale (~85k actes)
2. **Tribunal fédéral** (REST API) — Jurisprudence (~450k arrêts)
3. **Entscheidsuche** (Elasticsearch) — Décisions cantonales (~1.2M décisions)
4. **Droit cantonal** (Scraping) — GE, VD, NE, FR, VS, JU

## ✅ Fait

- [x] Recherche et validation du nom "Soluris" (étymologie, RDAP, trademark check)
- [x] Création repo GitHub O-N-2950/soluris
- [x] Architecture complète frontend + backend
- [x] Design system v1 (dark mode tech — abandonné)
- [x] Design system v2 (premium éditorial Navy + Or — actif)
- [x] Logo : SVG vectoriel recréé + PNG fond transparent (3 tailles)
- [x] Intégration logo dans le site
- [x] Push GitHub complet

## 🔲 À Faire

- [ ] **Déployer sur Railway** (besoin du Railway API token ou déploiement manuel)
- [ ] **Enregistrer soluris.ch** (confirmé disponible)
- [ ] **Configurer variables d'environnement** : ANTHROPIC_API_KEY, JWT_SECRET, DATABASE_URL
- [ ] **Implémenter RAG complet** : embeddings Cohere, recherche vectorielle dans legal_chunks
- [ ] **Pipeline d'ingestion** : scraper Fedlex + Entscheidsuche → legal_documents → chunking → embedding
- [ ] **Adapter app.html et login.html** au nouveau design system premium
- [ ] **Tests** : API endpoints, auth flow, chat flow
- [ ] **Mobile responsive** : tester et ajuster sur iPhone/Android

## 📝 Décisions Techniques

| Date | Décision | Raison |
|------|----------|--------|
| 2026-02-21 | FastAPI over Django | Async natif, plus rapide, auto OpenAPI docs |
| 2026-02-21 | Vanilla HTML/CSS/JS over React | Moins de dépendances, plus rapide à déployer sur Railway |
| 2026-02-21 | asyncpg over psycopg2 | Native async, meilleure perf avec FastAPI |
| 2026-02-21 | JWT over sessions | Stateless, scalable |
| 2026-02-21 | Design v1→v2 | Logo premium ≠ site "startup tech", alignement nécessaire |
| 2026-02-21 | Cormorant Garamond (serif) | Autorité institutionnelle pour la cible avocats |

## 🔑 Environnement

| Variable | Source | Status |
|----------|--------|--------|
| DATABASE_URL | Railway (auto) | ⏳ Pas encore déployé |
| ANTHROPIC_API_KEY | User | ⏳ À configurer |
| JWT_SECRET | Généré (openssl rand -hex 32) | ⏳ À configurer |
| ANTHROPIC_MODEL | Default: claude-sonnet-4-20250514 | ✅ Codé en dur |

---
*Dernière mise à jour : 2026-02-21 — Session : redesign premium éditorial, intégration logo, CONTEXT.md auto-update activé*
