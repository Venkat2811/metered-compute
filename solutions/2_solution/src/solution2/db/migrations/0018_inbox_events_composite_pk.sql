ALTER TABLE cmd.inbox_events
  DROP CONSTRAINT IF EXISTS inbox_events_pkey;

ALTER TABLE cmd.inbox_events
  ADD PRIMARY KEY (event_id, consumer_name);
