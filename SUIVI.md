# SOLURIS — Suivi d'avancement précis

> Mise à jour : 2026-03-01
> Légende : ✅ Fait et en production | 🟡 Code écrit, ingestion pas faite | ❌ Pas encore fait

---

## 🔴 BLOQUANTS IMMÉDIATS (rien ne fonctionne sans ça)

| # | Tâche | Statut | Notes |
|---|-------|--------|-------|
| B1 | Configurer `COHERE_API_KEY` sur Railway | ❌ | Bloque UNIQUEMENT la génération d'embeddings (étape après ingestion) |
| B2 | Configurer `ANTHROPIC_API_KEY` sur Railway | ❌ | Bloque UNIQUEMENT le chat utilisateur (pas le scraping/ingestion) |
| B3 | Configurer `TAIX_INTERNAL_KEY` sur Railway | ❌ | Bloque UNIQUEMENT l'intégration tAIx |
| **NB** | **Scraping + ingestion = zéro clé API requise** | ✅ | On peut ingérer tout le contenu maintenant |

---

## PHASE 1 — Données en base de production

### 1A. Droit fédéral (Fedlex)
| # | Tâche | Statut | Détail |
|---|-------|--------|--------|
| 1A-1 | Scraper `fedlex.py` — code | ✅ Code OK | 22 codes, 5 973 articles identifiés |
| 1A-2 | Ingestion Fedlex → Railway PostgreSQL | ❌ PAS FAIT | Le script tourne localement, jamais lancé en prod |
| 1A-3 | Vérifier données dans DB prod | ❌ | `SELECT COUNT(*) FROM legal_documents` — à faire |

**Codes à ingérer en priorité** : CO, CC, CP, CPC, CPP, LP, LTF, LDIP, LAT, LEI, Cst, LFus, LPGA, LAVS, LAMal, LIFD, LHID, LT, LTVA, LPP, OPP3, OFPr

### 1B. Jurisprudence TF (Entscheidsuche)
| # | Tâche | Statut | Détail |
|---|-------|--------|--------|
| 1B-1 | Scraper `entscheidsuche.py` — code | ✅ Code OK | 5 697 ATF FR + 57 875 BGer FR accessibles |
| 1B-2 | Scraper mode `--mode fiscal` | ✅ Code OK | Cible IIe Cour TF + filtrage mots-clés fiscaux |
| 1B-3 | Ingestion ATF → Railway PostgreSQL | ❌ PAS FAIT | Jamais lancé en prod |
| 1B-4 | Ingestion BGer fiscal → Railway PostgreSQL | ❌ PAS FAIT | |

### 1C. Embeddings & Recherche Vectorielle
| # | Tâche | Statut | Détail |
|---|-------|--------|--------|
| 1C-1 | Extension pgvector activée en prod | ✅ Fait | Railway PostgreSQL pgvector/pg16 |
| 1C-2 | Colonne `embedding VECTOR(1024)` dans `legal_chunks` | ✅ Schema OK | Défini dans `db/database.py` |
| 1C-3 | Script batch embedding `embed_chunks.py` | ✅ Code OK | |
| 1C-4 | Générer embeddings Cohere en prod | ❌ PAS FAIT | Bloqué par B2 (COHERE_API_KEY) |
| 1C-5 | Index IVFFlat/HNSW sur colonne embedding | ❌ PAS FAIT | À faire après embeddings |
| 1C-6 | Tester recherche vectorielle en prod (<500ms) | ❌ PAS FAIT | |

### 1D. Lois Fiscales Cantonales (26 cantons)
| Canton | Méthode | Code scraper | Données ingérées |
|--------|---------|-------------|-----------------|
| GE (Genève) | HTML direct | ✅ `cantonal_tax.py` | ❌ PAS FAIT |
| VD (Vaud) | HTML direct | ✅ | ❌ |
| NE (Neuchâtel) | HTML direct | ✅ | ❌ |
| FR (Fribourg) | HTML direct | ✅ | ❌ |
| JU (Jura) | HTML direct | ✅ | ❌ |
| VS (Valais) | HTML direct | ✅ | ❌ |
| TI (Tessin) | HTML direct | ✅ | ❌ |
| BE (Berne) | HTML direct | ✅ | ❌ |
| ZH (Zurich) | HTML direct | ✅ | ❌ |
| BS (Bâle-Ville) | HTML direct | ✅ | ❌ |
| BL (Bâle-Campagne) | HTML direct | ✅ | ❌ |
| SO (Soleure) | HTML direct | ✅ | ❌ |
| AG (Argovie) | HTML direct | ✅ | ❌ |
| LU (Lucerne) | HTML direct | ✅ | ❌ |
| ZG (Zoug) | HTML direct | ✅ | ❌ |
| SG (Saint-Gall) | HTML direct | ✅ | ❌ |
| TG (Thurgovie) | HTML direct | ✅ | ❌ |
| GR (Grisons) | HTML direct | ✅ | ❌ |
| GL (Glaris) | HTML direct | ✅ | ❌ |
| SZ (Schwyz) | PDF | ✅ | ❌ |
| SH (Schaffhouse) | Manuel (PDF indirect) | ✅ | ❌ |
| NW (Nidwald) | Manuel | ✅ | ❌ |
| OW (Obwald) | Manuel | ✅ | ❌ |
| UR (Uri) | Manuel | ✅ | ❌ |
| AI (Appenzell R-I) | Manuel | ✅ | ❌ |
| AR (Appenzell R-E) | Manuel | ✅ | ❌ |

**Circulaires AFC** : n°1, 8, 18, 25, 31 — ✅ cataloguées | ❌ PAS ingérées

### 1E. RAG & Citations
| # | Tâche | Statut | Détail |
|---|-------|--------|--------|
| 1E-1 | Service RAG `rag.py` | ✅ Code OK | Prompt structuré, citations, grounding |
| 1E-2 | Score de confiance cosinus | ✅ Code OK | Seuil anti-hallucination |
| 1E-3 | RAG fonctionnel en prod | ❌ PAS FAIT | Bloqué par B1 + absence de données |
| 1E-4 | Tester : 90% réponses avec citations | ❌ PAS FAIT | |

### 1F. Infrastructure Prod
| # | Tâche | Statut | Détail |
|---|-------|--------|--------|
| 1F-1 | Déploiement Railway | ✅ Fait | https://soluris-web-production.up.railway.app |
| 1F-2 | Auth JWT (login/signup) | ✅ Fait | Fonctionnel |
| 1F-3 | Quota enforcement (plans) | ✅ Code OK | Essentiel/Pro/Cabinet |
| 1F-4 | Essai gratuit 7 jours | ✅ Code OK | trial_expires_at |
| 1F-5 | Endpoint `/api/fiscal-query` (tAIx) | ✅ Code OK | |
| 1F-6 | Domaine soluris.ch configuré | ❌ | Domaine acheté, pas encore pointé sur Railway |

---

## PHASE 2 — Différenciation (après Phase 1 opérationnelle)

| # | Tâche | Statut |
|---|-------|--------|
| 2.1 | Filtres canton & domaine dans RAG | ❌ |
| 2.2 | Droit cantonal romand général (6 cantons GE/VD/NE/FR/VS/JU) | ❌ |
| 2.3 | Export Word/PDF des conversations | ❌ |
| 2.4 | Organisation en dossiers | ❌ |
| 2.5 | Paiement Stripe | ❌ |

---

## PHASE 3 — Avantage compétitif
| # | Tâche | Statut |
|---|-------|--------|
| 3.1 | Upload documents (PDF/DOCX) | ❌ |
| 3.2 | Templates juridiques (mise en demeure, résiliation bail...) | ❌ |
| 3.3 | Mode adversarial (contre-arguments) | ❌ |
| 3.4 | Veille juridique automatique (RSS TF) | ❌ |

---

## 🎯 Prochaines actions dans l'ordre

1. **[2h]** Lancer `fedlex.py` → ingérer 5 973 articles fédéraux en prod *(aucune clé requise)*
2. **[3h]** Lancer `entscheidsuche.py` → ingérer ATF en prod *(aucune clé requise)*
3. **[4h]** Lancer `cantonal_tax.py` canton par canton → ingérer lois fiscales *(aucune clé requise)*
4. **[5 min]** Configurer `COHERE_API_KEY` sur Railway
5. **[2h]** Lancer `embed_chunks.py` → générer embeddings Cohere en prod
6. **[5 min]** Configurer `ANTHROPIC_API_KEY` + `TAIX_INTERNAL_KEY` sur Railway
7. **[1h]** Tester RAG end-to-end avec question fiscale réelle
8. **[1h]** Configurer domaine soluris.ch → Railway

---

*Mise à jour automatique à chaque session Claude*

