-- Append-only event log + read tables (cards, comments, orders).

CREATE TABLE IF NOT EXISTS events (
  seq          BIGSERIAL PRIMARY KEY,
  event_id     TEXT   NOT NULL UNIQUE,
  session_id   TEXT   NOT NULL,
  type         TEXT   NOT NULL,
  ts_ms        BIGINT NOT NULL,
  ingested_ms  BIGINT NOT NULL,
  body         JSONB  NOT NULL
);
CREATE INDEX IF NOT EXISTS events_session_ts  ON events (session_id, ts_ms);
CREATE INDEX IF NOT EXISTS events_session_seq ON events (session_id, seq);
CREATE INDEX IF NOT EXISTS events_type_ts     ON events (type, ts_ms);

CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_no_update ON events;
CREATE TRIGGER events_no_update BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION events_append_only();
DROP TRIGGER IF EXISTS events_no_truncate ON events;
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
  FOR EACH STATEMENT EXECUTE FUNCTION events_append_only();

CREATE OR REPLACE FUNCTION events_notify() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('events', NEW.session_id || ':' || NEW.seq);
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_notify_insert ON events;
CREATE TRIGGER events_notify_insert AFTER INSERT ON events
  FOR EACH ROW EXECUTE FUNCTION events_notify();

-- Read tables, rebuilt from events by the projector in the same transaction.
CREATE TABLE IF NOT EXISTS cards (
  session_id TEXT    NOT NULL,
  card_id    TEXT    NOT NULL,
  version    INTEGER NOT NULL,
  ts_ms      BIGINT  NOT NULL,
  status     TEXT    NOT NULL,
  sku        TEXT,
  price      BIGINT,
  event_id   TEXT    NOT NULL,
  body       JSONB   NOT NULL,
  PRIMARY KEY (session_id, card_id, version)
);
CREATE INDEX IF NOT EXISTS cards_session_ts ON cards (session_id, ts_ms);

CREATE TABLE IF NOT EXISTS comments (
  session_id   TEXT   NOT NULL,
  comment_id   TEXT   NOT NULL,
  ts_ms        BIGINT NOT NULL,
  user_ref     TEXT   NOT NULL,
  text         TEXT   NOT NULL,
  label        TEXT,
  action       TEXT,
  labeled_body JSONB,
  PRIMARY KEY (session_id, comment_id)
);
CREATE INDEX IF NOT EXISTS comments_session_ts ON comments (session_id, ts_ms);

CREATE TABLE IF NOT EXISTS orders (
  session_id      TEXT    NOT NULL,
  order_id        TEXT    NOT NULL,
  state           TEXT    NOT NULL,
  approval_needed BOOLEAN NOT NULL,
  amount          BIGINT  NOT NULL,
  updated_ts_ms   BIGINT  NOT NULL,
  body            JSONB   NOT NULL,
  PRIMARY KEY (session_id, order_id)
);
CREATE INDEX IF NOT EXISTS orders_session_state ON orders (session_id, state);
