"""PostgreSQL database with pgvector support"""
import os
import asyncio
import asyncpg
import logging

log = logging.getLogger("soluris.db")

pool: asyncpg.Pool = None

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/soluris")
# Railway uses postgres:// but asyncpg needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


async def init_db():
    global pool
    
    # Retry connection up to 5 times (DB may still be starting)
    for attempt in range(5):
        try:
            pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
            break
        except Exception as e:
            log.warning(f"DB connection attempt {attempt+1}/5 failed: {e}")
            if attempt < 4:
                await asyncio.sleep(3)
            else:
                log.error("Could not connect to database — running in degraded mode")
                return

    try:
        async with pool.acquire() as conn:
            # Extensions
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            log.info("✅ pgcrypto + pgvector extensions activated")

            # ── Users ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    plan TEXT DEFAULT 'trial',
                    trial_expires_at TIMESTAMPTZ,
                    queries_this_month INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # ── Conversations ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT DEFAULT 'Nouvelle conversation',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
            """)

            # ── Messages ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    sources JSONB DEFAULT '[]'::jsonb,
                    tokens_used INTEGER DEFAULT 0,
                    rag_chunks INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
            """)

            # ── Legal Documents ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS legal_documents (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source TEXT NOT NULL,
                    external_id TEXT,
                    doc_type TEXT,
                    title TEXT,
                    reference TEXT,
                    jurisdiction TEXT DEFAULT 'CH',
                    language TEXT DEFAULT 'fr',
                    content TEXT,
                    publication_date DATE,
                    url TEXT,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(source, external_id)
                );
                CREATE INDEX IF NOT EXISTS idx_doc_source ON legal_documents(source);
                CREATE INDEX IF NOT EXISTS idx_doc_type ON legal_documents(doc_type);
            """)

            # ── Legal Chunks (with pgvector) ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS legal_chunks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    document_id UUID REFERENCES legal_documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    source_ref TEXT,
                    source_url TEXT,
                    embedding vector(1024),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_chunk_doc ON legal_chunks(document_id);
            """)

            # ── Migrate: BYTEA → vector(1024) if needed ──
            col_type = await conn.fetchval("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'legal_chunks' AND column_name = 'embedding'
            """)
            if col_type == "bytea":
                log.info("🔄 Migrating legal_chunks.embedding from BYTEA to vector(1024)...")
                await conn.execute("ALTER TABLE legal_chunks DROP COLUMN embedding;")
                await conn.execute("ALTER TABLE legal_chunks ADD COLUMN embedding vector(1024);")
                log.info("✅ Migration complete")

            # ── HNSW index for fast similarity search ──
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunk_embedding_hnsw
                ON legal_chunks USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 200);
            """)

            # Stats
            doc_count = await conn.fetchval("SELECT COUNT(*) FROM legal_documents")
            chunk_count = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks")
            embedded_count = await conn.fetchval("SELECT COUNT(*) FROM legal_chunks WHERE embedding IS NOT NULL")
            log.info(f"📊 DB stats: {doc_count} documents, {chunk_count} chunks, {embedded_count} embedded")

        log.info("✅ Database initialized with pgvector")
    except Exception as e:
        log.error(f"Database initialization failed: {e}")


async def get_db() -> asyncpg.Connection:
    async with pool.acquire() as conn:
        yield conn
