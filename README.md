# coffee-wiki

An Indian Specialty Coffee Index & Wiki that tracks single-origin and blend offerings across Indian specialty roasters, links them to canonical coffee estates, and records historical price and stock snapshots.

## Architecture

```text
coffee-wiki/
├── db/
│   ├── schema.sql          # PostgreSQL / Supabase schema, RLS policies, and account-deletion RPC
│   └── seed.sql            # Canonical roasters, estates, aliases, sample offerings, and snapshots
├── pipeline/               # Python ingestion, LLM/rule extraction, entity resolution, and DB sync
│   └── data/
│       ├── raw/            # Raw roaster catalog JSON dumps (gitignored)
│       └── processed/      # Normalized output artifacts (gitignored)
└── web/                    # Frontend web app (Vite / React / Supabase client)
