CREATE TABLE IF NOT EXISTS botw_compendium (item_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                                                                                                 entry_name VARCHAR(255) UNIQUE,
                                                                                                                         entry_number INTEGER, upgradeable BOOLEAN DEFAULT false);


CREATE INDEX IF NOT EXISTS item_name_idx ON botw_compendium (item_name);


CREATE TABLE IF NOT EXISTS botw_inventories (user_id BIGINT PRIMARY KEY,
                                                                    item_id INTEGER REFERENCES botw_compendium(item_id),
                                                                                               item_level SMALLINT, quantity INTEGER DEFAULT 0);


CREATE TABLE IF NOT EXISTS botw_upgrade_path (item_id INTEGER REFERENCES botw_compendium(item_id),
                                                                         item_upgrade_level SMALLINT NOT NULL,
                                                                                                     upgrade_item_one INTEGER REFERENCES botw_compendium(item_id),
                                                                                                                                         upgrade_item_one_quantity INTEGER NOT NULL,
                                                                                                                                                                           upgrade_item_two INTEGER REFERENCES botw_compendium(item_id),
                                                                                                                                                                                                               upgrade_item_two_quantity INTEGER, upgrade_item_three INTEGER REFERENCES botw_compendium(item_id),
                                                                                                                                                                                                                                                                                        upgrade_item_three_quantity INTEGER, PRIMARY KEY (item_id,
                                                                                                                                                                                                                                                                                                                                          item_upgrade_level));
