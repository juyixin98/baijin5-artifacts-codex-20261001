-- Schema for the variant resource service.
-- A resource owns many representations; each representation is one
-- (media type + media parameters, language, charset) tuple with a body.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS resources (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS representations (
  id              TEXT PRIMARY KEY,
  resource_id     TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  ordinal         INTEGER NOT NULL,
  media_type      TEXT NOT NULL,            -- e.g. application
  media_subtype   TEXT NOT NULL,            -- e.g. json / vnd.shop.v2+json
  media_params    TEXT NOT NULL DEFAULT '{}', -- JSON object, pre-q parameters
  language        TEXT NOT NULL,            -- canonical lower-case BCP 47 tag
  charset         TEXT NOT NULL DEFAULT 'utf-8',
  body            TEXT NOT NULL,
  UNIQUE (resource_id, ordinal),
  CHECK (ordinal >= 0)
);

CREATE INDEX IF NOT EXISTS idx_representations_resource ON representations(resource_id);
