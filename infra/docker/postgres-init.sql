-- Runs once when the Postgres volume is first created. The runtime role must be able to log in;
-- the migration grants it table privileges and row-level security applies to it (it owns nothing).
CREATE ROLE ci_app LOGIN PASSWORD 'ci_app';
