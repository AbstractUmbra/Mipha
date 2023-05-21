DROP TABLE IF EXISTS todos;
CREATE TABLE todos (
    todo_id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    todo_content TEXT NOT NULL,
    todo_reminder TIMESTAMP WITH TIME ZONE,
    todo_created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);
