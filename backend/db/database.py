"""PostgreSQL database with pgvector support"""
import os
import asyncpg

pool: asyncpg.Pool = None

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/soluris")
# Railway uses postgres:// but asyncpg needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)

    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                plan TEXT DEFAULT 'trial',
                queries_this_month INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                sources JSONB DEFAULT '[]'::jsonb,
                tokens_used INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
        """)

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
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS legal_chunks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                document_id UUID REFERENCES legal_documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                source_ref TEXT,
                source_url TEXT,
                embedding BYTEA,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_chunk_doc ON legal_chunks(document_id);
        """)

    print("✅ Database initialized")


async def get_db() -> asyncpg.Connection:
    async with pool.acquire() as conn:
        yield conn
