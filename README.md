# Soluris — L'IA juridique suisse

**soluris.ch** · La solution du droit suisse par l'intelligence artificielle.

## Stack technique

- **Frontend** : HTML/CSS/JS vanilla, design system dark-mode premium
- **Backend** : FastAPI (Python 3.11), async
- **Base de données** : PostgreSQL + pgvector (embeddings)
- **IA** : Claude API (Anthropic) pour la génération
- **Sources** : Fedlex (SPARQL), Tribunal fédéral, Entscheidsuche, cantons romands
- **Déploiement** : Railway (Docker)

## Variables d'environnement

| Variable | Description |
|----------|------------|
| `DATABASE_URL` | URL PostgreSQL (fourni par Railway) |
| `ANTHROPIC_API_KEY` | Clé API Anthropic |
| `JWT_SECRET` | Secret pour les tokens JWT |
| `ANTHROPIC_MODEL` | Modèle Claude (default: claude-sonnet-4-20250514) |

## Développement local

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://localhost:5432/soluris"
export ANTHROPIC_API_KEY="sk-..."
export JWT_SECRET="dev-secret"
uvicorn backend.main:app --reload --port 8000
```

## Licence

Propriétaire — © 2026 Soluris, Genève, Suisse
