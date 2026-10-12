-- One Postgres server, two databases: `agent` (LangGraph checkpoints, created by POSTGRES_DB) and `langfuse`.
CREATE DATABASE langfuse OWNER agent;
