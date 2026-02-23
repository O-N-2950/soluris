# SOLURIS — Contexte Projet

> Fichier mis à jour automatiquement à chaque session. Sert de mémoire persistante entre les conversations.

## Dernière mise à jour : 2026-02-23 (RAG implémenté + Security fixes + Analyse concurrentielle)

## 🎯 Vision

**Soluris** = "Solutio" (solution) + "Iuris" (du droit). Plateforme d'intelligence juridique suisse propulsée par l'IA. Cible : avocats, études, magistrats en Suisse romande.

- **Domaine choisi** : `soluris.ch` (confirmé disponible via RDAP — pas encore enregistré)
- **Positionnement** : Premium, institutionnel, "la référence de confiance"
- **Pricing** : Essentiel CHF 89/mo, Pro CHF 149/mo, Cabinet CHF 349/mo, Enterprise sur mesure. Essai 7j gratuit sans CB.
- **Positionnement recommandé** : Entité séparée (Soluris SA) pour crédibilité juridique et levée de fonds possible

## 🏟️ Analyse Concurrentielle (23 fév 2026)

### Silex (Ex Nunc Intelligence) — CONCURRENT PRINCIPAL
- **Funding** : $2.15M pre-seed (oversubscribed), led by Spicehaus Partners
- **Équipe** : 10+ personnes, CEO avocate (Me Kyriaki Bongard), cofondatrice Zoé Berry
- **Origine** : EPFL Innovation Park, Lausanne
- **Status** : LIVE — centaines de cabinets d'avocats, notaires, départements juridiques
- **Stack** : Propriétaire — pipeline IA re-engineered from scratch, pas un wrapper LLM
- **Forces** : Base de données propriétaire (fédérale + cantonale), zéro hallucination revendiqué, intégration Agora (Geste Informatique)
- **Faiblesses** : Pricing non publié (probablement cher), pas d'écosystème multi-services
- **Prix** : Primé PERL, Venture Kick 1&2, FIT Digital, EPFL Booster, top 3 ZKB Pionierpreis

### Swiss-Noxtua (Helbing Lichtenhahn + Noxtua Berlin)
- **Funding** : Backed par C.H.Beck (DE), MANZ (AT), Helbing Lichtenhahn (CH) — gros éditeurs juridiques
- **Status** : En développement, liste d'attente sur swiss-noxtua.ch
- **Forces** : Accès exclusif aux Commentaires romands + Basler Kommentare, ISO 42001/27001 certifié, 4 langues
- **Faiblesses** : Pas encore lancé, gros consortium = lent, approche top-down
- **Centre tech** : Berlin + nouveau centre CH prévu

### SwissLegalAI
- **Status** : Actif
- **Forces** : Gestion documentaire complète (même manuscrits), intégrations Outlook/Teams/SharePoint, podcast IA des dossiers
- **Pricing** : Sur mesure
- **Faiblesses** : Moins de profondeur juridique pure que Silex

### Ailegis
- **Équipe** : 4 personnes (2 business + 2 ML)
- **Status** : Prototype avancé, basé sur OpenAI
- **Forces** : Anonymisation de texte juridique, focus SME
- **Faiblesses** : Petit, pas de traction visible, prototype

### Lexplorer
- **Forces** : Recherche sémantique de jurisprudence (comprend le sens, pas juste mots-clés)
- **Faiblesses** : Focus narrow (search only), pas d'assistant conversationnel

### Autres acteurs
- **REF-Lex** (FER Genève) : IA spécialisée droit du travail, générateurs de documents
- **Weblaw AI** : Contenus Jusletter, formations, pas d'outil IA direct
- **Law·rence** : Mise en relation avec avocats (marketplace, pas IA juridique)

## 💡 Avantages Uniques Soluris

1. **Écosystème Groupe NEO** : Seule legal tech qui peut cross-sell TournePage (divorce), WIN WIN (assurances), MATCHO (fiduciaires), immo.cool (immobilier). Chaque client d'une app NEO = prospect Soluris.
2. **Claude API** : Qualité supérieure en français juridique vs OpenAI. Coût maîtrisé (~CHF 30/mo pour 10k requêtes avec Haiku 4.5).
3. **Positionnement prix** : Silex vise le premium. Soluris peut capturer le mid-market (avocats solo, petites études) à CHF 89/mo.
4. **Agilité** : Pas de consortium, pas de comité. Ship fast, iterate avec feedback direct des utilisateurs.

## 🏗 Stack Technique

| Composant | Technologie |
|-----------|-------------|
| Frontend | HTML/CSS/JS vanilla (pas de framework) |
| Backend | FastAPI (Python 3.11) |
| Base de données | PostgreSQL + pgvector |
| Auth | JWT (python-jose, bcrypt, 72h expiration) |
| IA | Claude Haiku 4.5 (claude-haiku-4-5-20251001) — ~30 CHF/mo pour 10k req |
| Embeddings | Cohere multilingual-v3 (prévu, pas encore implémenté) |
| Scraping | SPARQLWrapper + BeautifulSoup (Fedlex SPARQL) |
| Hébergement | Railway |
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



## 🚀 Implémentation RAG (23 fév 2026)

### Fichiers créés/modifiés
1. **backend/services/rag.py** — Pipeline RAG complet :
   - `embed_text()` : Embedding question via Cohere multilingual-v3
   - `embed_texts_batch()` : Batch embedding pour ingestion
   - `search_legal_chunks()` : Recherche vectorielle pgvector (cosine similarity, seuil 0.35)
   - `format_chunks_as_context()` : Formatage des chunks pour injection dans prompt Claude
   - `generate_answer()` : Pipeline complet (embed → search → inject → Claude → parse sources)
   - 2 modes : SYSTEM_PROMPT_WITH_RAG (contexte juridique injecté) et SYSTEM_PROMPT_NO_RAG (fallback)

2. **backend/db/database.py** — pgvector activé :
   - `CREATE EXTENSION vector`
   - `legal_chunks.embedding vector(1024)` (migration auto BYTEA → vector)
   - Index HNSW pour recherche rapide
   - `trial_expires_at` ajouté dans users
   - `rag_chunks` ajouté dans messages
   - Stats au démarrage

3. **backend/scripts/embed_chunks.py** — Batch embedding :
   - `python -m backend.scripts.embed_chunks` (nouveaux chunks)
   - `python -m backend.scripts.embed_chunks --all` (re-embed tout)
   - `python -m backend.scripts.embed_chunks --stats` (stats)
   - Batch 96, rate limiting, retry on error

4. **backend/scripts/ingest_fedlex.py** — Ingestion Fedlex → DB :
   - `python -m backend.scripts.ingest_fedlex` (tous les JSON)
   - `python -m backend.scripts.ingest_fedlex --scrape` (scrape + ingest)
   - Gère documents + chunks, upsert on conflict

5. **requirements.txt** — Ajout `cohere==5.13.4` + `pgvector==0.3.6`

### Pipeline d'activation complet
```bash
# 1. Scraper les 15 codes prioritaires
python -m backend.scrapers.fedlex --mode priority

# 2. Ingérer dans PostgreSQL
python -m backend.scripts.ingest_fedlex

# 3. Générer les embeddings Cohere
COHERE_API_KEY=xxx python -m backend.scripts.embed_chunks

# 4. Vérifier
python -m backend.scripts.embed_chunks --stats
```

### Variables d'environnement requises
- `COHERE_API_KEY` : Clé API Cohere (obtenir sur dashboard.cohere.com)
- `ANTHROPIC_API_KEY` : Déjà configuré
- `DATABASE_URL` : Déjà configuré (Railway PostgreSQL)

### Sécurité corrigée
- ✅ CORS wildcard Railway retiré (main.py) — utilise RAILWAY_PUBLIC_DOMAIN env var

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
│   └── assets/              ← Logos SVG/PNG
├── backend/
│   ├── main.py              ← FastAPI entry, CORS (restricted), static serving
│   ├── db/database.py       ← asyncpg pool + pgvector + HNSW index
│   ├── routers/
│   │   ├── auth.py          ← JWT login/signup/me
│   │   ├── chat.py          ← RAG endpoint /api/chat
│   │   ├── conversations.py ← History /api/conversations
│   │   └── health.py        ← /health check
│   ├── services/rag.py      ← Pipeline RAG complet (Cohere + pgvector + Claude)
│   ├── scripts/
│   │   ├── embed_chunks.py  ← Batch embedding Cohere multilingual-v3
│   │   └── ingest_fedlex.py ← Ingestion JSON Fedlex → PostgreSQL
│   └── scrapers/
│       ├── fedlex.py        ← SPARQL scraper complet (list/scrape/priority modes)
│       └── entscheidsuche.py ← Court decisions API
├── data/
│   └── fedlex/              ← JSON scrapés (gitignored)
├── Dockerfile
├── railway.toml
├── requirements.txt
├── TODO.md                  ← Plan d'implémentation détaillé
├── README.md
└── CONTEXT.md               ← Ce fichier
```

## 🗃 Base de Données (Schema)

- **users** : id (UUID), email, name, password_hash, plan, queries_this_month
- **conversations** : id (UUID), user_id → users, title, timestamps
- **messages** : id (SERIAL), conversation_id → conversations, role, content, sources (JSONB), tokens_used
- **legal_documents** : id, source, external_id, doc_type, title, reference, jurisdiction, language, content, publication_date, url, metadata (JSONB)
- **legal_chunks** : id, document_id → legal_documents, chunk_index, chunk_text, source_ref, source_url, embedding (BYTEA → à migrer en VECTOR)

## 🔴 Gap Critique (audit 23 fév 2026)

Le RAG n'est PAS implémenté. Dans `backend/services/rag.py`, ligne ~30 :
```python
# TODO: Add RAG retrieval here
# 1. Embed the question using Cohere multilingual
# 2. Search legal_chunks by vector similarity
# 3. Prepend relevant chunks to system prompt
# For now, we rely on Claude's knowledge of Swiss law
```

Sans RAG, Soluris = wrapper Claude avec un bon prompt. Valeur = ~0.
Avec RAG + 500 lois + 5000 arrêts ATF = vrai outil juridique. Valeur = CHF 89-349/mo × milliers d'avocats.

## 🗺 Roadmap Prioritaire

### Phase 1 — Parité minimale (semaines 1-4) ← PRIORITÉ ABSOLUE
- [x] Activer pgvector dans PostgreSQL Railway ✅ (database.py mis à jour)
- [x] Migrer legal_chunks.embedding de BYTEA vers VECTOR(1024) ✅ (migration auto dans database.py)
- [ ] Exécuter fedlex.py --mode priority (ingérer les 15 codes principaux)
- [ ] Ajouter cohere aux requirements, implémenter batch embedding
- [ ] Implémenter recherche vectorielle dans rag.py
- [ ] Réécrire system prompt pour grounding strict
- [ ] Ingérer 5'000 arrêts ATF via entscheidsuche.py
- [ ] Tester réduction hallucinations (score confiance cosinus)

### Phase 2 — Lancement beta (semaines 5-8)
- [ ] Essai gratuit 7 jours (trial_expires_at dans users)
- [ ] Stripe intégration (plans Essentiel/Pro/Cabinet)
- [ ] Enregistrer soluris.ch
- [ ] Beta privée avec 10 avocats romands
- [ ] Citations interactives dans le frontend (clic → texte complet)

### Phase 3 — Scale (mois 3-6)
- [ ] Droit cantonal (26 cantons)
- [ ] Doctrine (si partenariat éditeur)
- [ ] Export Word/PDF des recherches
- [ ] API pour intégration dans logiciels d'avocats
- [ ] Création Soluris SA

## ⚠️ Sécurité (audit 23 fév 2026)
- CORS trop permissif : `https://*.up.railway.app` → restreindre à l'URL exacte de prod
- Pas de rate limiting → ajouter slowapi
- Pas de crash monitor → copier le pattern de TournePage/MATCHO

## 🔗 Groupe NEO
Soluris fait partie de l'écosystème Groupe NEO. Synergies identifiées :
- Client TournePage (divorce) → prospect Soluris (questions juridiques)
- Fiduciaire MATCHO → prospect Soluris (droit fiscal, commercial)
- Client WIN WIN → prospect Soluris (droit des assurances)
