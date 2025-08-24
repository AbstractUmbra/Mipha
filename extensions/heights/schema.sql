CREATE TABLE IF NOT EXISTS heights (
    user_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    height REAL NOT NULL
);

INSERT OR IGNORE INTO heights VALUES
    (1, 'MewTwo', 201),
    (2, 'Steve Jobs', 188),
    (3, 'Zero Two', 177);
