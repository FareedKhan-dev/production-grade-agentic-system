-- Database schema for the application, as PostgreSQL DDL.
-- The SQLModel classes in src/data/models create these tables at startup, and
-- tests/test_schema.py checks that this file matches them. LangGraph's checkpointer
-- and mem0 create their own tables the first time the agent runs.

CREATE TABLE "user" (
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id SERIAL NOT NULL,
	email VARCHAR NOT NULL,
	hashed_password VARCHAR NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_user_email ON "user" (email);

CREATE TABLE session (
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR NOT NULL,
	user_id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE INDEX ix_session_user_id ON session (user_id);
