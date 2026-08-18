CREATE TABLE IF NOT EXISTS heights (
    user_id BIGINT NOT NULL,
    scope_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    height REAL NOT NULL,

    PRIMARY KEY (scope_id, user_id)
);
