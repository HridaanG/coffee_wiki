-- coffee-wiki PostgreSQL / Supabase Schema

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Roasters
CREATE TABLE IF NOT EXISTS public.roasters (
  id text PRIMARY KEY,
  name text NOT NULL,
  city text,
  website_url text,
  platform text CHECK (platform IN ('shopify', 'woocommerce', 'manual')),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 2. Estates
CREATE TABLE IF NOT EXISTS public.estates (
  id text PRIMARY KEY,
  name text NOT NULL,
  producer_name text,
  region text NOT NULL,
  sub_region text,
  state text,
  altitude_min_masl int,
  altitude_max_masl int,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 3. Entity Aliases
CREATE TABLE IF NOT EXISTS public.entity_aliases (
  alias text PRIMARY KEY CHECK (alias = lower(alias)),
  entity_type text NOT NULL CHECK (entity_type IN ('estate', 'region', 'varietal', 'process')),
  canonical_id text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_type_canonical
  ON public.entity_aliases (entity_type, canonical_id);

-- 4. Offerings
CREATE TABLE IF NOT EXISTS public.offerings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  roaster_id text NOT NULL REFERENCES public.roasters(id) ON DELETE CASCADE,
  estate_id text REFERENCES public.estates(id) ON DELETE SET NULL,
  source_product_id text NOT NULL,
  title text NOT NULL,
  source_url text UNIQUE NOT NULL,
  is_blend boolean NOT NULL DEFAULT false,
  region text,
  altitude_min_masl int,
  altitude_max_masl int,
  varietals text[] NOT NULL DEFAULT '{}',
  process text,
  roast_level text,
  tasting_notes_raw text[] NOT NULL DEFAULT '{}',
  flavor_categories text[] NOT NULL DEFAULT '{}' CHECK (
    flavor_categories <@ ARRAY[
      'Fruity',
      'Floral',
      'Sweet & Sugary',
      'Nutty & Cocoa',
      'Spices',
      'Roasted & Toasty',
      'Green & Vegetative',
      'Fermented & Boozy'
    ]::text[]
  ),
  in_stock boolean NOT NULL DEFAULT true,
  min_price_inr numeric(10,2),
  price_250g_inr numeric(10,2),
  extraction_confidence text NOT NULL DEFAULT 'medium' CHECK (extraction_confidence IN ('high', 'medium', 'low')),
  content_hash text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_offerings_roaster_id ON public.offerings (roaster_id);
CREATE INDEX IF NOT EXISTS idx_offerings_estate_id ON public.offerings (estate_id);
CREATE INDEX IF NOT EXISTS idx_offerings_in_stock ON public.offerings (in_stock);
CREATE INDEX IF NOT EXISTS idx_offerings_flavor_categories ON public.offerings USING GIN (flavor_categories);
CREATE INDEX IF NOT EXISTS idx_offerings_varietals ON public.offerings USING GIN (varietals);

-- 5. Offering Snapshots
CREATE TABLE IF NOT EXISTS public.offering_snapshots (
  id bigserial PRIMARY KEY,
  offering_id uuid NOT NULL REFERENCES public.offerings(id) ON DELETE CASCADE,
  in_stock boolean NOT NULL,
  min_price_inr numeric(10,2),
  price_250g_inr numeric(10,2),
  scraped_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snapshots_offering_time
  ON public.offering_snapshots (offering_id, scraped_at DESC);

-- 6. User Coffee Logs
CREATE TABLE IF NOT EXISTS public.user_coffee_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  offering_id uuid NOT NULL REFERENCES public.offerings(id) ON DELETE CASCADE,
  status text NOT NULL CHECK (status IN ('tried', 'wishlist')),
  rating int CHECK (rating BETWEEN 1 AND 5),
  personal_note text,
  tried_at date DEFAULT current_date,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, offering_id)
);

CREATE INDEX IF NOT EXISTS idx_user_coffee_logs_user_id ON public.user_coffee_logs (user_id);

-- ============================================================================
-- Row Level Security (RLS) & Privileges
-- ============================================================================

ALTER TABLE public.roasters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.estates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.entity_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.offerings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.offering_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_coffee_logs ENABLE ROW LEVEL SECURITY;

-- Public read-only catalog grants
GRANT SELECT ON public.roasters TO anon, authenticated;
GRANT SELECT ON public.estates TO anon, authenticated;
GRANT SELECT ON public.entity_aliases TO anon, authenticated;
GRANT SELECT ON public.offerings TO anon, authenticated;
GRANT SELECT ON public.offering_snapshots TO anon, authenticated;

-- User log privileges
REVOKE ALL ON public.user_coffee_logs FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_coffee_logs TO authenticated;

-- Public catalog RLS policies
DROP POLICY IF EXISTS "Public read roasters" ON public.roasters;
CREATE POLICY "Public read roasters"
  ON public.roasters FOR SELECT
  TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS "Public read estates" ON public.estates;
CREATE POLICY "Public read estates"
  ON public.estates FOR SELECT
  TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS "Public read entity_aliases" ON public.entity_aliases;
CREATE POLICY "Public read entity_aliases"
  ON public.entity_aliases FOR SELECT
  TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS "Public read offerings" ON public.offerings;
CREATE POLICY "Public read offerings"
  ON public.offerings FOR SELECT
  TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS "Public read offering_snapshots" ON public.offering_snapshots;
CREATE POLICY "Public read offering_snapshots"
  ON public.offering_snapshots FOR SELECT
  TO anon, authenticated
  USING (true);

-- User coffee logs RLS policies (owner-only)
DROP POLICY IF EXISTS "Users select own coffee logs" ON public.user_coffee_logs;
CREATE POLICY "Users select own coffee logs"
  ON public.user_coffee_logs FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users insert own coffee logs" ON public.user_coffee_logs;
CREATE POLICY "Users insert own coffee logs"
  ON public.user_coffee_logs FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users update own coffee logs" ON public.user_coffee_logs;
CREATE POLICY "Users update own coffee logs"
  ON public.user_coffee_logs FOR UPDATE
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users delete own coffee logs" ON public.user_coffee_logs;
CREATE POLICY "Users delete own coffee logs"
  ON public.user_coffee_logs FOR DELETE
  TO authenticated
  USING (auth.uid() = user_id);

-- ============================================================================
-- Hardened Account Deletion RPC
-- ============================================================================

CREATE OR REPLACE FUNCTION public.delete_own_account()
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, auth AS $$
BEGIN
  IF auth.uid() IS NULL THEN RAISE EXCEPTION 'Not authenticated'; END IF;
  DELETE FROM auth.users WHERE id = auth.uid();
END;
$$;
REVOKE EXECUTE ON FUNCTION public.delete_own_account() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.delete_own_account() TO authenticated;