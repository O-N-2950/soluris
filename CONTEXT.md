# SOLURIS — Contexte Projet

> Fichier mis à jour automatiquement à chaque session. Sert de mémoire persistante entre les conversations.

## 🎯 Vision

**Soluris** = "Solutio" (solution) + "Iuris" (du droit). Plateforme d'intelligence juridique suisse propulsée par l'IA. Cible : avocats, études, magistrats en Suisse romande.

- **Domaine choisi** : `soluris.ch` (confirmé disponible — pas encore enregistré)
- **Positionnement** : Premium, institutionnel, "la référence de confiance"
- **Pricing** : Essentiel CHF 89/mo, Pro CHF 149/mo, Cabinet CHF 349/mo. Essai 7j gratuit sans CB.
- **Concurrent principal** : Silex (Ex Nunc Intelligence) — CHF 120/mo, EPFL spin-off

## 🏗 Stack Technique

| Composant | Technologie |
|-----------|-------------|
| Frontend | HTML/CSS/JS vanilla |
| Backend | FastAPI (Python 3.11) |
| Base de données | PostgreSQL + pgvector |
| Auth | JWT (python-jose, bcrypt, 72h expiration) |
| IA | Claude Haiku 4.5 (~30 CHF/mo pour 10k req) |
| Embeddings | Cohere multilingual-v3 (1024 dim) |
| Hébergement | Railway (PostgreSQL pgvector/pg16 + FastAPI) |
| Repo | https://github.com/O-N-2950/soluris |

## 🎨 Design System (v2 — Premium Éditorial)

**Palette :** Navy deep `#06101F`, Navy `#0B1F3B`, Or `#C6A75E`, Cream `#F5F0E8`, Text secondary `#8A9AB5`
**Typographie :** Cormorant Garamond (titres), DM Sans (corps), JetBrains Mono (code)
**Esthétique :** "Banque privée genevoise" — éditoriale, lignes fines dorées, espacement généreux.

## 📁 Structure du Projet

```
soluris/
├── frontend/
│   ├── index.html          ← Landing page
│   ├── app.html             ← Interface chat
│   ├── login.html           ← Auth
│   ├── css/styles.css       ← Design system (1450+ lignes)
│   ├── js/{app,auth,landing}.js
│   └── assets/logo-*.{svg,png}
├── backend/
│   ├── main.py              ← FastAPI entry, CORS, static serving
│   ├── db/database.py       ← asyncpg pool, schema init
│   ├── routers/
│   │   ├── auth.py          ← JWT login/signup/me
│   │   ├── chat.py          ← RAG endpoint /api/chat (+ quota + filtres)
│   │   ├── conversations.py ← History
│   │   ├── fiscal.py        ← /api/fiscal-query (tAIx integration) ← NOUVEAU
│   │   └── health.py        ← /health
│   ├── services/
│   │   ├── rag.py           ← Claude API + vector retrieval + citations
│   │   ├── embeddings.py    ← Cohere/OpenAI embedding service
│   │   └── ingestion.py     ← PostgreSQL bulk insert
│   ├── scripts/
│   │   ├── ingest_fedlex.py ← JSON → PostgreSQL
│   │   └── embed_chunks.py  ← Batch embedding Cohere
│   └── scrapers/
│       ├── fedlex.py        ← SPARQL (5 973 articles, 22 codes — +7 fiscaux)
│       ├── entscheidsuche.py ← Elasticsearch API + scrape_fiscal_atf()
│       └── cantonal_tax.py  ← Lois fiscales 26 cantons + circulaires AFC ← NOUVEAU
├── data/
│   ├── fedlex/              ← JSON scrapés (gitignored)
│   ├── jurisprudence/       ← JSON scrapés (gitignored)
│   └── cantonal_tax/        ← JSON lois fiscales cantonales (gitignored)
├── Dockerfile
├── railway.toml
├── requirements.txt
└── CONTEXT.md
```

## 🗃 Données Juridiques Disponibles

### Législation fédérale (Fedlex) — ✅ FAIT
- Source : API SPARQL `fedlex.data.admin.ch/sparqlendpoint`
- **22 codes prioritaires** : CO, CC, CP, CPC, CPP, LP, LTF, LDIP, LAT, LEI, Cst, LFus, LPGA, LAVS, LAMal + **LIFD, LHID, LT (impôt anticipé), LTVA, LPP, OPP3, OFPr** (ajoutés 2026-03-01)
- **5 973 articles** extraits avec chunking article-level
- Métadonnées : RS number, section path, article number, fedlex URL

### Jurisprudence TF (Entscheidsuche) — ✅ FAIT
- Source : API Elasticsearch `entscheidsuche.ch/_search.php`
- **5 697 ATF** publiés en français (arrêts de principe)
- **57 875 arrêts BGer** FR (tous les arrêts)
- Nouveau mode `--mode fiscal` : cible CH_BGer_002 (IIe Cour de droit public) + filtrage mots-clés fiscaux

### Lois fiscales cantonales — 🆕 SCRAPER PRÊT (ingestion à faire)
- Source : Portails officiels des 26 cantons
- Scraper `cantonal_tax.py` : 26 cantons catalogués
  - **17 cantons** : scrape HTML direct (GE, VD, NE, FR, JU, VS, TI, BE, ZH, BS, BL, SO, AG, LU, ZG, SG, TG, GR, GL)
  - **1 canton** : scrape PDF (SZ)
  - **8 cantons** : mode manual (SH, NW, OW, UR, AI, AR — lien PDF indirect)
- Circulaires AFC cataloguées : n°1, 8, 18, 25, 31

### Données encore à ingérer
- Droit cantonal romand général (6 cantons : GE, VD, NE, FR, VS, JU) — Phase 2.2
- Tribunal administratif fédéral (25k FR)
- Tribunal pénal fédéral (3.7k FR)

## 🔌 API Routes

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/auth/login` | Email/password → JWT |
| POST | `/api/auth/signup` | Création compte → JWT |
| GET | `/api/auth/me` | Info user |
| POST | `/api/chat` | Question → RAG + Claude → réponse sourcée |
| GET | `/api/conversations` | Liste conversations |
| GET | `/api/conversations/{id}` | Messages d'une conversation |
| DELETE | `/api/conversations/{id}` | Supprimer |
| **POST** | **`/api/fiscal-query`** | **RAG fiscal pour tAIx (clé interne)** ← NOUVEAU |
| GET | `/api/fiscal-query/ping` | Sanity check endpoint fiscal |
| GET | `/health` | Healthcheck Railway |

## 🤝 Intégration tAIx (juraitax)

**Objectif** : tAIx cite les articles de loi exacts pour chaque déduction fiscale suggérée.

**Architecture** :
```
tAIx (juraitax) → POST /api/fiscal-query → Soluris RAG → Claude
                  {question, canton, annee, internal_key}
                  ← {reponse, sources: [{reference, titre, url}], confidence}
```

**Exemple de réponse Soluris vers tAIx** :
```json
{
  "reponse": "Le montant maximum déductible pour le pilier 3a est de CHF 7'056 (Art. 7 OPP3)...",
  "sources": [
    {"reference": "Art. 82 LPP", "titre": "Cotisations du salarié", "url": "...fedlex..."},
    {"reference": "Art. 7 OPP3", "titre": "Montant de la déduction", "url": "...fedlex..."}
  ],
  "confidence": 0.94,
  "canton": "GE",
  "domain": "droit_fiscal"
}
```

**Variable d'env requise** : `TAIX_INTERNAL_KEY` (à configurer dans Railway)

## 🚀 Déploiement

- **URL production** : https://soluris-web-production.up.railway.app
- **Railway project** : `soluris` (ID: d03ee6e4-0aab-457d-af2a-015b3a5b196d)
- **Services** :
  - `postgres` : pgvector/pgvector:pg16 + volume persistent
  - `soluris-web` : Dockerfile → FastAPI/uvicorn, auto-deploy depuis GitHub main
- **Variables requises** : DATABASE_URL, JWT_SECRET, ANTHROPIC_API_KEY (manquante), COHERE_API_KEY (manquante), **TAIX_INTERNAL_KEY** (nouveau, pour tAIx)
- **Domaine Railway** : soluris-web-production.up.railway.app
- **Custom domain** : soluris.ch (pas encore configuré — domaine pas encore acheté)

## 📊 Progression TODO

- [x] Phase 1.1 : Ingestion Fedlex — 5 973 articles, 15 codes (+ 7 fiscaux ajoutés)
- [x] Phase 1.2 : Scraper jurisprudence TF — 5 697 ATF FR accessibles + mode fiscal
- [x] Phase 1.3 : Embeddings & RAG — Cohere multilingual-v3 + pgvector (code prêt)
- [x] Phase 1.4 : Citations vérifiables — prompt structuré + parsing sources
- [x] Phase 1.5 : Réduction hallucinations — grounding strict + score confiance
- [x] Phase 1.6 : Essai gratuit 7 jours — trial_expires_at + middleware
- [x] Phase 1.7 : Quota enforcement — plans Essentiel/Pro/Cabinet + compteur
- [x] Phase 1.8 : Landing page — pricing 89/149/349, essai 7j, badges souveraineté
- [x] Déploiement Railway — PostgreSQL pgvector + FastAPI, healthcheck OK
- [x] **Scraper cantonal_tax.py** — 26 cantons catalogués, 17 en HTML direct
- [x] **Endpoint /api/fiscal-query** — intégration tAIx, clé interne, RAG fiscal
- [ ] Ingestion données en production (fedlex + jurisprudence + lois fiscales cantonales)
- [ ] Configurer TAIX_INTERNAL_KEY dans Railway

---
*Dernière mise à jour : 2026-03-01 — Extension fiscale tAIx : scraper 26 cantons (cantonal_tax.py), endpoint /api/fiscal-query, +7 RS fiscaux fédéraux (LIFD, LHID, LPP, OPP3, LTVA...), mode fiscal ATF (IIe Cour TF)*
