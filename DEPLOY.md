# 🚀 Guide de Déploiement Soluris sur Railway

> **Dernière mise à jour** : 2 mars 2026  
> **Statut actuel** : ✅ LIVE sur `soluris-web-production.up.railway.app`

---

## 📋 Prérequis

- Un compte [Railway](https://railway.app) (gratuit pour commencer, plan Hobby $5/mois recommandé)
- Accès au repo GitHub `O-N-2950/soluris`
- Les clés API : Anthropic, Cohere (optionnel)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────┐
│              Railway Project                │
│                                             │
│  ┌──────────────┐    ┌──────────────────┐   │
│  │  Soluris Web │    │   PostgreSQL     │   │
│  │  (Dockerfile)│───▶│   + pgvector     │   │
│  │  Port 8000   │    │   (Plugin Rail)  │   │
│  └──────────────┘    └──────────────────┘   │
│                                             │
└─────────────────────────────────────────────┘
        │
        ▼
  https://soluris-web-production.up.railway.app
```

L'app a **2 services** :
1. **Soluris Web** — L'application Python/FastAPI (build via Dockerfile)
2. **PostgreSQL** — Base de données avec extension pgvector pour le RAG

---

## 🆕 Déploiement depuis zéro (nouveau projet)

### Étape 1 : Créer le projet Railway

1. Va sur [railway.app/new](https://railway.app/new)
2. Clique **"Deploy from GitHub repo"**
3. Autorise Railway à accéder au repo `O-N-2950/soluris`
4. Sélectionne le repo → Railway détecte automatiquement le `Dockerfile`
5. **NE CLIQUE PAS encore sur Deploy** — il faut d'abord la base de données

### Étape 2 : Ajouter PostgreSQL

1. Dans ton projet Railway, clique **"+ New"** → **"Database"** → **"PostgreSQL"**
2. Railway crée automatiquement une instance PostgreSQL
3. La variable `DATABASE_URL` est **automatiquement injectée** dans ton service web
4. **Important** : pgvector s'active automatiquement au premier lancement (voir `backend/db/database.py`)

### Étape 3 : Configurer les variables d'environnement

Dans Railway → ton service Soluris Web → onglet **"Variables"** → ajoute :

| Variable | Obligatoire | Description | Exemple |
|---|---|---|---|
| `DATABASE_URL` | ✅ | **Auto-injectée** par le plugin PostgreSQL | `postgresql://user:pass@host:5432/railway` |
| `ANTHROPIC_API_KEY` | ✅ | Clé API Anthropic pour le chat IA | `sk-ant-api03-...` |
| `JWT_SECRET` | ✅ | Secret pour les tokens d'authentification | Générer avec : `openssl rand -hex 32` |
| `NODE_ENV` | ✅ | Mode de l'application | `production` |
| `COHERE_API_KEY` | ⚡ | Pour les embeddings RAG (recommandé) | `co-...` |
| `OPENAI_API_KEY` | ❌ | Alternative à Cohere pour embeddings | `sk-...` |
| `ANTHROPIC_MODEL` | ❌ | Modèle Claude à utiliser | `claude-sonnet-4-20250514` (défaut) |
| `EMBEDDING_PROVIDER` | ❌ | `cohere` ou `openai` | `cohere` (défaut) |

**⚠️ MINIMUM VITAL pour que l'app démarre :**
```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx
JWT_SECRET=une-longue-chaine-aleatoire-de-64-caracteres
NODE_ENV=production
```

(`DATABASE_URL` est auto-fournie par le plugin PostgreSQL)

### Étape 4 : Déployer

1. Railway lance automatiquement le build dès que les variables sont configurées
2. Le build utilise le `Dockerfile` à la racine du repo
3. Le `railway.toml` configure :
   - Builder : Dockerfile
   - Health check : `/health` (timeout 120s)
   - Restart automatique en cas de crash (max 5 retries)
4. **Temps de build typique** : 2-3 minutes

### Étape 5 : Générer un domaine public

1. Dans Railway → service Soluris Web → onglet **"Settings"**
2. Section **"Networking"** → **"Generate Domain"**
3. Railway te donne une URL type `soluris-web-production.up.railway.app`
4. Pour un domaine custom (`soluris.ch`) : ajoute un CNAME dans ton DNS

---

## 🔄 Redéploiement (mise à jour du code)

Railway redéploie **automatiquement** à chaque push sur la branche `main` :

```bash
# 1. Fais tes modifications en local
git add .
git commit -m "fix: correction du bug X"
git push origin main

# 2. Railway détecte le push et relance le build automatiquement
# 3. Vérifie le déploiement dans le dashboard Railway ou :
curl https://soluris-web-production.up.railway.app/health
# → {"status":"ok","database":true,"service":"soluris"}
```

**Si le redéploiement ne se lance pas :**
- Vérifie dans Railway → service → "Deployments" que le trigger GitHub est actif
- Tu peux forcer un redéploiement : bouton **"Redeploy"** dans Railway

---

## 🔍 Résolution de problèmes courants

### ❌ "Build failed"

**Cause probable** : Erreur dans le Dockerfile ou dépendance manquante.

**Solution** :
1. Railway → Deployments → clique sur le build échoué → lis les logs
2. Erreurs courantes :
   - `pip install failed` → vérifie `requirements.txt`
   - `ModuleNotFoundError` → un package manque dans requirements.txt
   - `psycopg2` problème → on utilise `psycopg2-binary`, c'est dans requirements.txt

### ❌ "Health check failed" (l'app crash au démarrage)

**Cause probable** : Variable d'environnement manquante ou DB inaccessible.

**Solution** :
1. Railway → Deployments → clique sur le deploy → **"View Logs"**
2. Cherche les erreurs :
   - `ANTHROPIC_API_KEY` manquant → ajoute-le dans Variables
   - `connection refused` → le plugin PostgreSQL n'est pas lié au service
   - `JWT_SECRET` manquant → génère-en un (`openssl rand -hex 32`)

**Pour lier PostgreSQL au service :**
1. Clique sur le service PostgreSQL dans Railway
2. Onglet "Connect" → copie la `DATABASE_URL`
3. **OU mieux** : Railway → service web → Variables → "Add Reference" → sélectionne la DB

### ❌ "502 Bad Gateway" après déploiement

**Cause probable** : L'app n'écoute pas sur le bon port.

**Solution** : Le Dockerfile expose le port 8000 et uvicorn écoute sur 8000. Railway détecte automatiquement. Si problème, ajoute la variable :
```
PORT=8000
```

### ❌ "pgvector not available"

**C'est un WARNING, pas une erreur.** L'app tourne quand même. Le RAG par embeddings ne fonctionnera pas, mais le chat IA basique fonctionne.

Pour activer pgvector :
1. Railway utilise PostgreSQL 15+ qui supporte pgvector
2. L'extension s'active automatiquement au premier lancement (voir `database.py`)
3. Si ça échoue, connecte-toi à la DB et exécute : `CREATE EXTENSION IF NOT EXISTS vector;`

---

## 🗄️ Base de données

### Connexion directe

Railway → service PostgreSQL → onglet **"Connect"** → tu trouves :
- `DATABASE_URL` complète
- Host, port, user, password séparément
- Tu peux te connecter avec n'importe quel client PostgreSQL (pgAdmin, DBeaver, psql)

### Migrations

Les tables se créent **automatiquement** au démarrage dans `backend/db/database.py`. Pas besoin de lancer de migration manuelle.

Tables créées :
- `users` — Comptes utilisateurs
- `conversations` — Historique des conversations
- `messages` — Messages individuels
- `legal_chunks` — Articles de loi découpés pour le RAG
- `legal_embeddings` — Vecteurs d'embeddings (si pgvector actif)

### Ingestion des données juridiques

L'ingestion des articles Fedlex se fait via l'endpoint admin :

```bash
# Depuis un terminal (remplace l'URL et le token)
curl -X POST https://soluris-web-production.up.railway.app/api/admin/ingest \
  -H "Authorization: Bearer TON_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source": "fedlex"}'
```

---

## 📁 Structure des fichiers importants

```
soluris/
├── Dockerfile           ← Build instructions (Python 3.11-slim)
├── railway.toml         ← Configuration Railway (healthcheck, restart)
├── requirements.txt     ← Dépendances Python
├── backend/
│   ├── main.py          ← Point d'entrée FastAPI (uvicorn)
│   ├── db/
│   │   └── database.py  ← Connexion PostgreSQL + init tables
│   ├── routers/
│   │   ├── auth.py      ← Authentification JWT
│   │   ├── chat.py      ← Chat IA (Anthropic Claude)
│   │   ├── health.py    ← Endpoint /health
│   │   └── fiscal.py    ← Intégration tAIx
│   ├── services/
│   │   ├── rag.py       ← Retrieval Augmented Generation
│   │   ├── embeddings.py← Génération embeddings (Cohere/OpenAI)
│   │   └── ingestion.py ← Ingestion articles Fedlex
│   └── scrapers/        ← Scraping Fedlex, jurisprudence
└── frontend/
    ├── index.html       ← Landing page
    ├── app.html         ← Application chat
    ├── login.html       ← Page de connexion
    ├── css/             ← Styles
    └── js/              ← JavaScript frontend
```

---

## 🛠️ Développement local

```bash
# 1. Cloner le repo
git clone https://github.com/O-N-2950/soluris.git
cd soluris

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. PostgreSQL local (avec Docker)
docker run -d --name soluris-db \
  -e POSTGRES_DB=soluris \
  -e POSTGRES_PASSWORD=soluris \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# 4. Variables d'environnement (créer un fichier .env)
cat > .env << 'EOF'
DATABASE_URL=postgresql://postgres:soluris@localhost:5432/soluris
ANTHROPIC_API_KEY=sk-ant-api03-ta-cle-ici
JWT_SECRET=dev-secret-pas-pour-production
NODE_ENV=development
EOF

# 5. Lancer le serveur
export $(cat .env | xargs)
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# 6. Ouvrir dans le navigateur
open http://localhost:8000
```

---

## ✅ Checklist de déploiement

- [ ] Projet Railway créé avec lien GitHub
- [ ] Plugin PostgreSQL ajouté au projet
- [ ] `ANTHROPIC_API_KEY` configurée
- [ ] `JWT_SECRET` configurée (32+ caractères aléatoires)
- [ ] `NODE_ENV=production` configurée
- [ ] Domaine public généré dans Railway Settings
- [ ] `/health` retourne `{"status":"ok","database":true}`
- [ ] Page d'accueil accessible
- [ ] Chat IA fonctionnel (test avec une question juridique)

---

## 📞 Support

- **Olivier Neukomm** — CEO Groupe NEO — olivier@winwin.swiss
- **Railway docs** : https://docs.railway.app
- **FastAPI docs** : https://fastapi.tiangolo.com
