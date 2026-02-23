# SOLURIS — Plan d'Implémentation MVP

> Roadmap complète pour atteindre la parité compétitive avec Silex et lancer le MVP.
> Mise à jour : 2026-02-23

---

## 🔴 PHASE 1 — Parité Minimale (Semaines 1-4)
*Sans ça, aucun avocat ne paiera. Objectif : transformer le wrapper Claude en vrai outil juridique.*

### 1.1 Ingestion Fedlex — Législation fédérale
- [x] Explorer l'API SPARQL Fedlex (`https://fedlex.data.admin.ch/sparql`)
- [x] Écrire le scraper `backend/scrapers/fedlex.py` pour extraire le Recueil Systématique (RS)
- [x] Ingérer les codes principaux en priorité : CO, CC, CP, CPC, CPP, LP, LTF, LDIP, LAT, LEtr
- [x] Parser le HTML Fedlex → structurer dans `legal_documents` (PostgreSQL)
- [x] Chunking intelligent des articles (1 chunk = 1 article ou groupe d'alinéas cohérent)
- [x] Stocker les métadonnées : numéro RS, titre, langue, date de publication, URL source
- [x] Tester avec requête SPARQL de validation (compter les actes ingérés)
- **Critère de succès** : ≥ 500 lois fédérales principales ingérées et chunked

### 1.2 Ingestion Jurisprudence — Toute la Suisse francophone
- [x] Explorer l'API Elasticsearch entscheidsuche.ch (`_search.php`)
- [x] Écrire l'aspirateur massif `backend/scrapers/entscheidsuche.py`
- [x] Support HTML (BGer, BGE, cantons) + PDF (TAF, TPF) via PyMuPDF
- [x] Téléchargement parallèle (ThreadPoolExecutor, 20 workers, ~25 dec/s)
- [x] Pagination search_after pour corpus > 10k décisions
- [x] Chunking structuré : regeste / considérants / dispositif (max 2500 chars)
- [x] Détection automatique du domaine juridique (civil, pénal, public, social)
- [x] Tester avec 2 000 décisions (10 sources, 37 894 chunks, 80s) ✅
- [ ] **Lancer le scraping complet des 279k décisions (~3h)** ← PROCHAINE ACTION
- [ ] Stocker dans PostgreSQL `legal_documents` + `legal_chunks`
- **Données disponibles :**
  - ATF publiés : 5 697 | BGer : 57 875 | TAF : 25 558 | TPF : 3 766
  - Genève : 81 499 | Vaud : 81 789 | Fribourg : 11 601
  - Neuchâtel : 7 441 | Valais : 3 010 | Jura : 1 053
- **Critère de succès** : ≥ 279 000 décisions ingérées, ~2.6M chunks

### 1.3 Embeddings & Recherche Vectorielle (RAG)
- [ ] Choisir le modèle d'embeddings : Cohere multilingual-v3 ou OpenAI text-embedding-3-small
- [ ] Activer l'extension pgvector dans PostgreSQL (`CREATE EXTENSION vector`)
- [ ] Ajouter colonne `embedding VECTOR(1024)` dans `legal_chunks` (adapter dimension au modèle)
- [ ] Script de batch embedding : parcourir tous les chunks → générer embeddings → stocker
- [ ] Implémenter la recherche vectorielle dans `backend/services/rag.py` :
  - [ ] Recevoir la question utilisateur
  - [ ] Générer l'embedding de la question
  - [ ] Requête pgvector : `ORDER BY embedding <=> $1 LIMIT 10`
  - [ ] Retourner les chunks les plus pertinents avec leurs métadonnées
- [ ] Optimiser : index IVFFlat ou HNSW sur la colonne embedding
- **Critère de succès** : Recherche vectorielle retourne des résultats pertinents en < 500ms

### 1.4 Citations Vérifiables dans les Réponses
- [ ] Réécrire le system prompt Claude dans `rag.py` :
  - [ ] Instruction : "Base ta réponse UNIQUEMENT sur les sources fournies"
  - [ ] Instruction : "Cite chaque affirmation avec la référence exacte (art. X CO, ATF XXX III XX)"
  - [ ] Instruction : "Si tu ne trouves pas de source, dis-le explicitement"
  - [ ] Format de réponse structuré : réponse + liste des sources utilisées
- [ ] Injecter les chunks RAG dans le contexte du prompt (format structuré)
- [ ] Parser la réponse Claude pour extraire les citations → stocker dans `messages.sources` (JSONB)
- [ ] Afficher les citations dans le frontend : liens cliquables vers Fedlex / bger.ch
- [ ] Ajouter un badge "Sources vérifiées" sous chaque réponse sourcée
- **Critère de succès** : 90% des réponses juridiques contiennent ≥ 1 citation vérifiable

### 1.5 Réduction des Hallucinations
- [ ] Implémenter le "grounding strict" : Claude ne répond que si le RAG fournit du contenu pertinent
- [ ] Ajouter un score de confiance basé sur la distance cosinus des chunks récupérés
- [ ] Si score < seuil → message : "Je n'ai pas trouvé de source fiable pour cette question"
- [ ] Logger les questions sans résultats RAG pour identifier les lacunes de la base
- [ ] Tester avec 20 questions juridiques types et mesurer le taux d'hallucination
- **Critère de succès** : 0% de citations inventées sur le jeu de test

### 1.6 Essai Gratuit 7 Jours
- [ ] Ajouter champ `trial_expires_at TIMESTAMPTZ` dans table `users`
- [ ] À l'inscription : `trial_expires_at = NOW() + INTERVAL '7 days'`, `plan = 'trial'`
- [ ] Middleware quota : vérifier `trial_expires_at` avant chaque requête `/api/chat`
- [ ] Si trial expiré → réponse 402 + message "Votre essai gratuit est terminé"
- [ ] Frontend : afficher le nombre de jours restants dans la sidebar
- [ ] Frontend : bouton "Passer à un abonnement" quand trial expire
- [ ] Inscription sans carte bancaire (email + mot de passe suffit)
- **Critère de succès** : Flow complet inscription → 7 jours → expiration → upgrade prompt

### 1.7 Quota Enforcement (Plans Payants)
- [ ] Middleware `check_quota()` dans `backend/routers/chat.py` :
  - [ ] Vérifier `plan` et `queries_this_month` avant de traiter la requête
  - [ ] Trial : 50 requêtes max pendant 7 jours
  - [ ] Essentiel (CHF 89) : 200 requêtes/mois
  - [ ] Pro (CHF 149) : 1000 requêtes/mois
  - [ ] Cabinet (CHF 349) : illimité (5 users max)
- [ ] Cron job / scheduled task : reset `queries_this_month` le 1er de chaque mois
- [ ] Frontend : afficher compteur "X/200 requêtes utilisées ce mois"
- [ ] Réponse 429 quand quota dépassé + message clair
- **Critère de succès** : Impossible de dépasser le quota sans upgrade

### 1.8 Mise à Jour Landing Page
- [ ] Mettre à jour le pricing dans `frontend/index.html` :
  - [ ] Essentiel : CHF 89/mois (200 requêtes, droit fédéral + 2 cantons, essai 7j)
  - [ ] Pro : CHF 149/mois (1000 requêtes, tous les cantons disponibles, export)
  - [ ] Cabinet : CHF 349/mois (5 utilisateurs, illimité, dossiers, support prioritaire)
- [ ] Ajouter mention "Hébergé en Suisse" (SwissCenter) avec badge/icône drapeau suisse
- [ ] Ajouter section "Sources juridiques" : Fedlex, TF, droit cantonal romand
- [ ] Ajouter mention "Données jamais utilisées pour l'entraînement IA"
- [ ] Revoir les textes marketing pour refléter le vrai produit (pas de survente)
- **Critère de succès** : Landing page reflète fidèlement les capacités réelles du produit

---

## 🟡 PHASE 2 — Différenciation (Mois 2-3)
*Ce qui nous rend meilleur que "juste moins cher que Silex".*

### 2.1 Filtres Canton & Domaine du Droit
- [ ] Backend : ajouter `jurisdiction` et `legal_domain` dans les métadonnées des chunks
- [ ] API : paramètres optionnels `canton` et `domain` sur `/api/chat`
- [ ] RAG : filtrer les chunks par canton/domaine AVANT la recherche vectorielle
- [ ] Frontend : dropdown "Canton" sous la barre de chat (Fédéral, GE, VD, NE, FR, VS, JU)
- [ ] Frontend : dropdown "Domaine" (Civil, Pénal, Administratif, Fiscal, Travail, Bail, Famille)
- [ ] Persister les filtres dans le localStorage pour ne pas les re-sélectionner
- **Critère de succès** : Filtrer par canton modifie les résultats de manière cohérente

### 2.2 Droit Cantonal Romand — Législation (6 cantons)
*Note : La jurisprudence cantonale est déjà couverte par le scraper Entscheidsuche (Phase 1.2).*
*Cette section concerne la **législation cantonale** (lois, règlements).*
- [ ] Genève : scraper SilGenève (https://silgeneve.ch) → législation cantonale GE
- [ ] Vaud : scraper RSV (Recueil systématique vaudois)
- [ ] Neuchâtel : scraper RSN
- [ ] Fribourg : scraper législation fribourgeoise
- [ ] Valais : scraper législation valaisanne
- [ ] Jura : scraper législation jurassienne
- [ ] Pour chaque canton : ingestion → chunking → embedding → métadonnée `jurisdiction = 'GE'` etc.
- [ ] Tester avec des questions spécifiques par canton (ex: bail à Genève, impôts VD)
- **Critère de succès** : ≥ 100 textes principaux par canton romand ingérés

### 2.3 Export Word / PDF
- [ ] Backend : endpoint `POST /api/export/{conversation_id}` → génère fichier
- [ ] Format Word (.docx) avec python-docx :
  - [ ] En-tête : logo Soluris, date, référence du dossier
  - [ ] Corps : question + réponse formatée
  - [ ] Pied de page : sources juridiques citées avec liens
- [ ] Format PDF avec reportlab ou weasyprint (alternative)
- [ ] Frontend : bouton "Exporter" dans l'interface chat (icône document)
- [ ] Téléchargement direct du fichier
- **Critère de succès** : Export Word professionnel utilisable dans un mémoire juridique

### 2.4 Organisation en Dossiers
- [ ] Base de données : table `folders` (id, user_id, name, created_at)
- [ ] Relation : ajouter `folder_id` nullable dans table `conversations`
- [ ] API : CRUD `/api/folders` (create, list, update, delete)
- [ ] API : `PATCH /api/conversations/{id}` pour assigner à un dossier
- [ ] Frontend : sidebar avec liste des dossiers (+ "Sans dossier")
- [ ] Frontend : drag & drop ou menu contextuel pour assigner une conversation
- [ ] Frontend : icône dossier avec compteur de conversations
- **Critère de succès** : Avocat peut organiser ses conversations par affaire/client

### 2.5 Paiement Stripe
- [ ] Créer compte Stripe (mode test d'abord)
- [ ] Créer les 3 produits/prix dans Stripe Dashboard
- [ ] Backend : endpoint `POST /api/billing/create-checkout` → Stripe Checkout Session
- [ ] Backend : webhook `/api/billing/webhook` pour écouter les événements Stripe :
  - [ ] `checkout.session.completed` → activer le plan
  - [ ] `customer.subscription.updated` → changer de plan
  - [ ] `customer.subscription.deleted` → rétrograder en trial expiré
  - [ ] `invoice.payment_failed` → notifier l'utilisateur
- [ ] Frontend : bouton "S'abonner" redirige vers Stripe Checkout
- [ ] Frontend : page "Mon abonnement" avec gestion (upgrade, annuler)
- [ ] Tester le cycle complet en mode Stripe Test
- **Critère de succès** : Flow complet trial → paiement → abonnement actif → renouvellement

---

## 🟢 PHASE 3 — Avantage Compétitif (Mois 3-6)
*Ce que Silex ne fait pas encore — nos différenciateurs.*

### 3.1 Analyse de Documents Uploadés
- [ ] Backend : endpoint `POST /api/documents/upload` (accept .pdf, .docx, .txt)
- [ ] Extraction de texte : PyPDF2 pour PDF, python-docx pour Word
- [ ] Chunking du document uploadé → embeddings temporaires (session utilisateur)
- [ ] RAG enrichi : chercher dans le droit + dans le document uploadé
- [ ] Prompt spécialisé : "Analyse ce document au regard du droit suisse applicable"
- [ ] Frontend : zone de drag & drop dans l'interface chat
- [ ] Cas d'usage : analyser un contrat de bail, une décision judiciaire, un contrat de travail
- **Critère de succès** : Upload d'un bail → identification des clauses non-conformes

### 3.2 Templates & Génération de Documents
- [ ] Créer une bibliothèque de templates courants :
  - [ ] Mise en demeure (art. 107-109 CO)
  - [ ] Résiliation de bail (art. 266a ss CO)
  - [ ] Opposition à un commandement de payer (art. 74 LP)
  - [ ] Requête de mesures provisionnelles (art. 261 ss CPC)
  - [ ] Contrat de travail type (art. 319 ss CO)
- [ ] Backend : endpoint `POST /api/templates/generate` avec paramètres contextuels
- [ ] Claude génère le document pré-rempli avec le droit applicable (canton sélectionné)
- [ ] Export Word avec formatage professionnel
- **Critère de succès** : Génération d'une mise en demeure correcte en < 30 secondes

### 3.3 Mode Adversarial (Contre-Arguments)
- [ ] Option "Analyse contradictoire" dans l'interface
- [ ] Prompt Claude en 2 étapes :
  1. Répondre à la question du point de vue du client
  2. Identifier les contre-arguments que la partie adverse pourrait invoquer
- [ ] Affichage en 2 colonnes : "Vos arguments" / "Arguments adverses possibles"
- [ ] Sources citées des deux côtés
- **Critère de succès** : Différenciateur unique — aucun concurrent ne fait ça

### 3.4 Veille Juridique Automatique
- [ ] Scraper quotidien des nouveaux arrêts du TF (RSS bger.ch)
- [ ] Matcher les nouveaux arrêts avec les domaines suivis par chaque utilisateur
- [ ] Notification par email : "Nouvel arrêt du TF en droit du bail : ATF xxx"
- [ ] Dashboard "Veille" dans l'interface avec les derniers arrêts pertinents
- **Critère de succès** : Notification automatique dans les 24h d'un nouvel ATF pertinent

### 3.5 Multi-User Cabinet
- [ ] Table `teams` (id, name, owner_user_id, plan, max_users)
- [ ] Table `team_members` (team_id, user_id, role: admin/member)
- [ ] Quota partagé au niveau de l'équipe
- [ ] Admin peut inviter/retirer des membres
- [ ] Conversations privées par défaut, partageable au sein de l'équipe
- **Critère de succès** : 5 avocats d'un cabinet partagent un compte Cabinet

---

## 🔵 PHASE 4 — Écosystème (Mois 6+)

### 4.1 API Publique pour Intégrations
- [ ] Documentation OpenAPI / Swagger
- [ ] Clés API par utilisateur
- [ ] Rate limiting
- [ ] Potentiel : intégration avec logiciels de gestion d'étude (Winmacs, Winjur, Advoware)

### 4.2 Data Silos par Cabinet (Enterprise)
- [ ] Documents internes du cabinet vectorisés dans un espace isolé
- [ ] RAG cherche dans droit public + documents privés du cabinet
- [ ] Isolation stricte entre cabinets (multi-tenant avec partitionnement)

### 4.3 Soft Law
- [ ] Circulaires FINMA
- [ ] Directives AFC (Administration fédérale des contributions)
- [ ] Directives SEM (Secrétariat d'État aux migrations)
- [ ] Guidelines PFPDT (Protection des données)

### 4.4 Analytics Dashboard
- [ ] Statistiques d'usage : requêtes/jour, domaines les plus consultés
- [ ] Temps économisé estimé (basé sur benchmark : X min de recherche manuelle par requête)
- [ ] ROI calculator pour justifier l'abonnement

---

## ✅ DÉJÀ FAIT

- [x] Recherche et validation du nom "Soluris"
- [x] Création repo GitHub O-N-2950/soluris
- [x] Architecture complète frontend + backend (FastAPI, PostgreSQL, pgvector)
- [x] Design system v2 premium éditorial (Navy #0B1F3B + Or #C6A75E)
- [x] Logo : SVG + PNG (5 variantes)
- [x] Intégration logo dans le site
- [x] Auth JWT (login/signup/me)
- [x] Chat endpoint avec Claude Haiku 4.5
- [x] Historique des conversations
- [x] Schema DB : users, conversations, messages, legal_documents, legal_chunks
- [x] Push GitHub complet
- [x] Hébergement suisse confirmé (SwissCenter)
- [x] Analyse concurrentielle complète (Silex, LegesGPT, Swisslex, etc.)
- [x] Stratégie pricing définie (Essentiel 89, Pro 149, Cabinet 349)
- [x] Modèle IA choisi : Claude Haiku 4.5 (~30 CHF/mois pour 10k requêtes)
- [x] **Scraper Fedlex opérationnel** — 5 973 articles, 15 codes prioritaires
- [x] **Scraper Entscheidsuche v2 opérationnel** — aspirateur massif, 10 sources, HTML+PDF, 20 threads, 279k décisions FR accessibles, 2 000 testées avec succès

---

## 📊 Métriques de Suivi

| Métrique | Cible Phase 1 | Cible Phase 2 | Cible Phase 3 |
|----------|--------------|--------------|--------------|
| Lois fédérales ingérées | ≥ 500 (✅ 5 973) | ≥ 5 000 | ≥ 12 500 |
| Décisions de justice ingérées | ≥ 279 000 | ≥ 300 000 | ≥ 400 000 |
| Cantons jurisprudence | CH + 6 romands | + ZH, BE, TI | + tous |
| Chunks dans pgvector | ≥ 2 600 000 | ≥ 3 000 000 | ≥ 4 000 000 |
| Taux citation dans réponses | ≥ 90% | ≥ 95% | ≥ 95% |
| Taux hallucination | < 5% | < 2% | < 1% |
| Temps de réponse | < 5s | < 5s | < 3s |
| Utilisateurs test | 3-5 avocats | 10-20 | 50+ |

---

## 🏁 Prochaine Action Immédiate

**→ Tâche 1.1 : ✅ COMPLÉTÉE** — 5 973 articles extraits de 15 codes fédéraux

**→ Tâche 1.2 : ✅ SCRAPER OPÉRATIONNEL** — 279 289 décisions FR accessibles (fédéral + 6 cantons romands), 2 000 testées (37 894 chunks en 80s)

**→ MAINTENANT : Lancer le scraping complet + Tâche 1.3 Embeddings**

```bash
# 1. Scraping complet (~3h)
python -m backend.scrapers.entscheidsuche scrape --all

# 2. Données totales après scraping :
#    - 5 973 articles de loi (Fedlex)
#    - ~279 000 décisions de justice → ~2.6M chunks
#    = Base de données juridique la plus complète de Suisse romande
```

---

*Dernière mise à jour : 2026-02-23 — Scraper Entscheidsuche v2 massif : 279k décisions FR (fédéral + cantonal romand), HTML+PDF, 20 threads, testé 2000 dec → 37 894 chunks en 80s*
