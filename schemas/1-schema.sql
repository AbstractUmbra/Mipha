CREATE ROLE mipha WITH LOGIN PASSWORD 'your_password';
CREATE DATABASE mipha OWNER mipha;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- After connecting to the mipha db
CREATE TABLE IF NOT EXISTS lewd_config (
    guild_if BIGINT PRIMARY KEY,
    blacklist TEXT [],
    auto_six_digits BOOLEAN
);
CREATE TABLE IF NOT EXISTS guild_mod_config (
    id BIGINT PRIMARY KEY,
    automod_flags SMALLINT NOT NULL DEFAULT 0,
    broadcast_channel BIGINT,
    mention_count SMALLINT,
    safe_automod_entity_ids BIGINT ARRAY,
    mute_role_id BIGINT,
    muted_members BIGINT ARRAY,
    broadcast_webhook_url TEXT
);
CREATE TABLE IF NOT EXISTS profiles (
    user_id BIGINT PRIMARY KEY,
    nnid TEXT,
    fc_3ds TEXT,
    fc_switch TEXT,
    extra JSONB DEFAULT ('{}'::jsonb) NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id SERIAL PRIMARY KEY,
    expires TIMESTAMP WITH TIME ZONE,
    created TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    event TEXT,
    extra JSONB DEFAULT '{}'::JSONB
);
CREATE INDEX IF NOT EXISTS reminders_expires_idx ON reminders (expires);
CREATE TABLE IF NOT EXISTS starboard (
    id BIGINT PRIMARY KEY,
    channel_id BIGINT,
    threshold INTEGER DEFAULT 1 NOT NULL,
    locked BOOLEAN DEFAULT FALSE,
    max_age INTERVAL DEFAULT '7 days'::INTERVAL NOT NULL
);
CREATE TABLE IF NOT EXISTS starboard_entries (
    id SERIAL PRIMARY KEY,
    bot_message_id BIGINT UNIQUE NOT NULL,
    message_id BIGINT,
    channel_id BIGINT,
    author_id BIGINT,
    guild_id BIGINT REFERENCES starboard (id) ON DELETE CASCADE ON UPDATE NO ACTION NOT NULL
);
CREATE INDEX IF NOT EXISTS starboard_entries_bot_message_id_idx ON starboard_entries (bot_message_id);
CREATE INDEX IF NOT EXISTS starboard_entries_guild_id ON starboard_entries (guild_id);
CREATE TABLE IF NOT EXISTS starrers (
    id SERIAL PRIMARY KEY,
    author_id BIGINT NOT NULL,
    entry_id INTEGER REFERENCES starboard_entries (id) ON DELETE CASCADE ON UPDATE NO ACTION NOT NULL
);
CREATE INDEX IF NOT EXISTS starrers_entry_id_idx ON starrers (entry_id);
CREATE UNIQUE INDEX IF NOT EXISTS starrers_uniq_idx ON starrers (author_id, entry_id);
CREATE TABLE IF NOT EXISTS commands (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT,
    channel_id BIGINT,
    author_id BIGINT,
    used TIMESTAMP WITH TIME ZONE,
    prefix TEXT,
    command TEXT,
    app_command BOOLEAN NOT NULL DEFAULT FALSE,
    failed BOOLEAN
);
CREATE INDEX IF NOT EXISTS commands_guild_id_idx ON commands (guild_id);
CREATE INDEX IF NOT EXISTS commands_author_id_idx ON commands (author_id);
CREATE INDEX IF NOT EXISTS commands_used_idx ON commands (used);
CREATE INDEX IF NOT EXISTS commands_command_idx ON commands (command);
CREATE INDEX IF NOT EXISTS commands_app_command_idx ON commands (app_command);
CREATE INDEX IF NOT EXISTS commands_failed_idx ON commands (failed);
CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    name TEXT,
    content TEXT,
    owner_id BIGINT,
    uses INTEGER DEFAULT 0,
    location_id BIGINT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS tags_name_idx ON tags (name);
CREATE INDEX IF NOT EXISTS tags_location_id_idx ON tags (location_id);
CREATE INDEX IF NOT EXISTS tags_name_trgm_idx ON tags USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tags_name_lower_idx ON tags (LOWER(name));
CREATE UNIQUE INDEX IF NOT EXISTS tags_uniq_idx ON tags (LOWER(name), location_id);
CREATE TABLE IF NOT EXISTS tag_lookup (
    id SERIAL PRIMARY KEY,
    name TEXT,
    location_id BIGINT,
    owner_id BIGINT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    tag_id INTEGER REFERENCES tags (id) ON DELETE CASCADE ON UPDATE NO ACTION
);
CREATE INDEX IF NOT EXISTS tag_lookup_name_idx ON tag_lookup (name);
CREATE INDEX IF NOT EXISTS tag_lookup_location_id_idx ON tag_lookup (location_id);
CREATE INDEX IF NOT EXISTS tag_lookup_name_trgm_idx ON tag_lookup USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tag_lookup_name_lower_idx ON tag_lookup (LOWER(name));
CREATE UNIQUE INDEX IF NOT EXISTS tag_lookup_uniq_idx ON tag_lookup (LOWER(name), location_id);
CREATE TABLE IF NOT EXISTS tz_store (
    user_id BIGINT PRIMARY KEY,
    guild_ids BIGINT [],
    tz TEXT UNIQUE -- Spotted the issue now.
);
CREATE TABLE IF NOT EXISTS todos (
    id SERIAL PRIMARY KEY,
    owner_id BIGINT,
    content TEXT,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    jump_url TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id SERIAL PRIMARY KEY,
    target BIGINT,
    entry TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE TABLE IF NOT EXISTS family (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE,
    amount INTEGER NOT NULL
);
