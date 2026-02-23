# SOLURIS — Contexte Projet

> Fichier mis à jour automatiquement à chaque session. Sert de mémoire persistante entre les conversations.

## 🎯 Vision

**Soluris** = "Solutio" (solution) + "Iuris" (du droit). Plateforme d'intelligence juridique suisse propulsée par l'IA. Cible : avocats, études, magistrats en Suisse romande.

- **Domaine choisi** : `soluris.ch` (confirmé disponible via RDAP — pas encore enregistré)
- **Positionnement** : Premium, institutionnel, "la référence de confiance"
- **Pricing** : Essentiel CHF 89/mo, Pro CHF 149/mo, Cabinet CHF 349/mo, Enterprise sur mesure. Essai 7j gratuit sans CB.
- **Concurrent principal** : Silex (Ex Nunc Intelligence) — CHF 120/mo, EPFL spin-off, $2.15M levés, ~100s utilisateurs

## 🏗 Stack Technique

| Composant | Technologie |
|-----------|-------------|
| Frontend | HTML/CSS/JS vanilla (pas de framework) |
| Backend | FastAPI (Python 3.11) |
| Base de données | PostgreSQL + pgvector |
| Auth | JWT (python-jose, bcrypt, 72h expiration) |
| IA | Claude Haiku 4.5 (claude-haiku-4-5-20251001) — ~30 CHF/mo pour 10k req |
| Embeddings | Cohere multilingual-v3 (prévu, pas encore implémenté) |
| Hébergement | SwissCenter (Suisse) |
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
│       ├── fedlex.py        ← SPARQL scraper complet (list/scrape/priority modes)
│       └── entscheidsuche.py ← TF jurisprudence scraper (Elasticsearch API, ATF+BGer)
├── data/
│   ├── fedlex/              ← JSON scrapés (gitignored, regénérer avec --mode priority)
│   └── jurisprudence/       ← JSON scrapés (gitignored, regénérer avec --mode atf)
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

1. **Fedlex** (SPARQL + HTML filestore) — Législation fédérale consolidée. Endpoint: `fedlex.data.admin.ch/sparqlendpoint`. Ontologie JOLux. ~12 500 actes dans le RS, ~5 100 en vigueur. HTML structuré avec balises `<article>`. ✅ Scraper opérationnel.
2. **Tribunal fédéral / Entscheidsuche** (Elasticsearch API) — `entscheidsuche.ch/_search.php`. Index v2. Hiérarchie : CH_BGE_999 (ATF publiés, ~20k), CH_BGer (tous arrêts, ~175k), CH_BVGE (TAF, ~84k). Données multilingues (fr/de/it). Chunks structurés : regeste, considérants, dispositif. ✅ Scraper opérationnel.
3. **Entscheidsuche cantonale** — Mêmes API, index par canton (AG, GE, VD, etc.). ~600k+ décisions tous tribunaux confondus.
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
- [x] Analyse concurrentielle Silex (features, pricing, traction, tech stack)
- [x] Stratégie pricing révisée (Essentiel 89, Pro 149, Cabinet 349 — undercut Silex de 26%)
- [x] Modèle IA final : Claude Haiku 4.5 (~30 CHF/mois pour 10k requêtes)
- [x] Hébergement suisse confirmé : SwissCenter
- [x] Plan d'implémentation 4 phases créé (voir TODO.md)
- [x] **Scraper Fedlex opérationnel** (`backend/scrapers/fedlex.py`) — SPARQL + HTML parsing
- [x] **5 973 articles** extraits des 15 codes prioritaires (CO, CC, CP, CPC, CPP, LP, LTF, LDIP, LAT, LEI, Cst, LFus, LPGA, LAVS, LAMal)
- [x] Tâche 1.1 du TODO complétée : API SPARQL Fedlex explorée et intégrée

- [x] **Scraper Entscheidsuche opérationnel** (`backend/scrapers/entscheidsuche.py`) — API Elasticsearch, pagination search_after, parsing HTML structuré (regeste/considérants/dispositif)
- [x] **5 697 ATF (FR)** disponibles via API, 100 testés avec succès (1 409 chunks, 0 échecs). 175k+ arrêts BGer accessibles.
- [x] Tâche 1.2 du TODO en cours : API entscheidsuche explorée, scraper fonctionnel, données validées

## 🔲 À Faire

→ **Voir `TODO.md` pour le plan détaillé avec 4 phases et ~60 tâches.**

Résumé des phases :
1. **Phase 1 — Parité minimale** (Sem. 1-4) : Ingestion Fedlex + TF, RAG pgvector, citations, anti-hallucination, essai 7j, quota enforcement
2. **Phase 2 — Différenciation** (Mois 2-3) : Filtres canton/domaine, droit cantonal romand, export Word/PDF, dossiers, Stripe
3. **Phase 3 — Avantage compétitif** (Mois 3-6) : Upload de documents, templates juridiques, mode adversarial, veille juridique, multi-user
4. **Phase 4 — Écosystème** (Mois 6+) : API publique, data silos, soft law, analytics

## 📝 Décisions Techniques

| Date | Décision | Raison |
|------|----------|--------|
| 2026-02-21 | FastAPI over Django | Async natif, plus rapide, auto OpenAPI docs |
| 2026-02-21 | Vanilla HTML/CSS/JS over React | Moins de dépendances, plus rapide à déployer sur Railway |
| 2026-02-21 | asyncpg over psycopg2 | Native async, meilleure perf avec FastAPI |
| 2026-02-21 | JWT over sessions | Stateless, scalable |
| 2026-02-21 | Design v1→v2 | Logo premium ≠ site "startup tech", alignement nécessaire |
| 2026-02-21 | Cormorant Garamond (serif) | Autorité institutionnelle pour la cible avocats |
| 2026-02-23 | Claude Haiku 4.5 over Sonnet | 90% qualité, 1/3 du coût, rentable dès 1 client Essentiel |
| 2026-02-23 | Pricing agressif (89/149/349) | Undercut Silex (120 CHF), compétitif pour les petites études |
| 2026-02-23 | Essai 7j (pas 14j) | Aligné sur Silex, suffisant pour évaluer l'outil |
| 2026-02-23 | Phase 1 = RAG d'abord | Sans données juridiques = wrapper ChatGPT, aucun avocat ne paie |
| 2026-02-23 | Hébergement SwissCenter | Souveraineté des données suisse, argument commercial vs Silex |
| 2026-02-23 | Fedlex via SPARQL+HTML | API SPARQL pour métadonnées, filestore HTML pour le texte. ConsolidationAbstract→Consolidation→Expression→Manifestation. 5 973 articles extraits des 15 codes prioritaires |
| 2026-02-23 | Entscheidsuche via Elasticsearch | API `_search.php` avec pagination search_after. 5 697 ATF (FR) + 57k BGer (FR). Parsing HTML : regeste/considérants/dispositif. Chunks ~3000 chars max |

## 🔑 Environnement

| Variable | Source | Status |
|----------|--------|--------|
| DATABASE_URL | SwissCenter (PostgreSQL) | ⏳ À configurer |
| ANTHROPIC_API_KEY | User | ⏳ À configurer |
| JWT_SECRET | Généré (openssl rand -hex 32) | ⏳ À configurer |
| ANTHROPIC_MODEL | claude-haiku-4-5-20251001 | ✅ Décidé |
| COHERE_API_KEY | Pour embeddings | ⏳ À obtenir |

## 🏆 Analyse Concurrentielle (Résumé)

| | Silex | Soluris (cible MVP) |
|---|---|---|
| Prix | CHF 120/mo | CHF 89/mo (Essentiel) |
| Base juridique | Fédéral + 26 cantons + soft law | Fédéral + 6 cantons romands |
| Jurisprudence | TF + cantonale | TF (+ cantonale Phase 2) |
| Citations sources | ✅ | ✅ (Phase 1) |
| Hébergement CH | ✅ | ✅ SwissCenter |
| Export Word/PDF | ✅ | Phase 2 |
| Intégration Agora | ✅ | ❌ |
| Upload documents | En dev | Phase 3 |
| Mode adversarial | ❌ | Phase 3 (différenciateur) |
| Équipe | 10+ personnes, $2.15M | 1 développeur |

---
*Dernière mise à jour : 2026-02-23 — Session : scraper Entscheidsuche (jurisprudence TF) opérationnel, 5 697 ATF FR + 175k BGer accessibles via Elasticsearch API, parsing HTML structuré (regeste/considérants/dispositif)*
