-- Input consumption is committed with its order/message effects, including no-effect inputs.
CREATE TABLE IF NOT EXISTS attachment_consumed_inputs (
  session_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES events(event_id),
  PRIMARY KEY(session_id,event_id)
);
CREATE TRIGGER attachment_inputs_no_update BEFORE UPDATE OR DELETE ON attachment_consumed_inputs
  FOR EACH ROW EXECUTE FUNCTION events_append_only();
