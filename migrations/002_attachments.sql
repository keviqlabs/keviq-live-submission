-- Attachment history is in events; projection and server-owned source authority are separate from request bodies.
CREATE UNIQUE INDEX IF NOT EXISTS events_attachment_version
  ON events (session_id, (body->>'attachment_id'), ((body->>'version')::bigint))
  WHERE type = 'comment_attachment';

CREATE TABLE IF NOT EXISTS attachments (
  session_id TEXT NOT NULL, attachment_id TEXT NOT NULL, version BIGINT NOT NULL,
  event_id TEXT NOT NULL UNIQUE, body JSONB NOT NULL, summary JSONB NOT NULL,
  PRIMARY KEY (session_id, attachment_id)
);
CREATE TABLE IF NOT EXISTS attachment_bindings (
  session_id TEXT PRIMARY KEY, body JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS attachment_binding_history (
  session_id TEXT NOT NULL, revision BIGINT NOT NULL, body JSONB NOT NULL,
  PRIMARY KEY (session_id, revision)
);
CREATE TABLE IF NOT EXISTS attachment_source_receipts (
  event_id TEXT PRIMARY KEY REFERENCES events(event_id), session_id TEXT NOT NULL, body JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS attachment_source_session ON attachment_source_receipts(session_id);
CREATE TABLE IF NOT EXISTS attachment_order_intents (
  session_id TEXT NOT NULL, comment_id TEXT NOT NULL, order_id TEXT NOT NULL,
  PRIMARY KEY(session_id,comment_id), UNIQUE(session_id,order_id)
);
DROP TRIGGER IF EXISTS attachment_binding_history_no_update ON attachment_binding_history;
CREATE TRIGGER attachment_binding_history_no_update BEFORE UPDATE OR DELETE ON attachment_binding_history
  FOR EACH ROW EXECUTE FUNCTION events_append_only();
DROP TRIGGER IF EXISTS attachment_receipts_no_update ON attachment_source_receipts;
CREATE TRIGGER attachment_receipts_no_update BEFORE UPDATE OR DELETE ON attachment_source_receipts
  FOR EACH ROW EXECUTE FUNCTION events_append_only();
CREATE OR REPLACE FUNCTION registered_attachment_writer() RETURNS trigger AS $$
DECLARE scope TEXT := current_setting('keviq.attachment_write_scope', true);
BEGIN
  IF EXISTS(SELECT 1 FROM attachment_bindings WHERE session_id=NEW.session_id) THEN
    IF scope IS NULL OR scope NOT IN ('server','source') THEN
      RAISE EXCEPTION 'registered session requires an authorized writer';
    END IF;
    IF scope='source' AND NEW.type IN ('comment_attachment','order','message_out') THEN
      RAISE EXCEPTION 'source writer cannot create a server decision';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS events_attachment_writer ON events;
CREATE TRIGGER events_attachment_writer BEFORE INSERT ON events FOR EACH ROW
  EXECUTE FUNCTION registered_attachment_writer();
CREATE OR REPLACE FUNCTION attachment_binding_notify() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('events', NEW.session_id || ':binding');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS attachment_binding_changed ON attachment_bindings;
CREATE TRIGGER attachment_binding_changed AFTER INSERT OR UPDATE ON attachment_bindings
  FOR EACH ROW EXECUTE FUNCTION attachment_binding_notify();
