CREATE TABLE IF NOT EXISTS sources (
  source_id UUID PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  title TEXT,
  publisher TEXT,
  fetched_at TIMESTAMPTZ,
  content_hash TEXT,
  raw_path TEXT,
  license_note TEXT
);

CREATE TABLE IF NOT EXISTS claims (
  claim_id UUID PRIMARY KEY,
  source_id UUID REFERENCES sources(source_id),
  claim_type TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_external TEXT,
  object_kind TEXT,
  object_external TEXT,
  start_date DATE,
  end_date DATE,
  date_start TIMESTAMPTZ,
  date_end TIMESTAMPTZ,
  role TEXT,
  confidence DOUBLE PRECISION,
  extracted_json JSONB
);

CREATE TABLE IF NOT EXISTS entity_resolution (
  resolution_id UUID PRIMARY KEY,
  claim_id UUID REFERENCES claims(claim_id),
  resolved_person_id UUID,
  resolved_group_id UUID,
  match_method TEXT,
  score DOUBLE PRECISION,
  review_status TEXT
);
