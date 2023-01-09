INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'champion''s tunic'
        ),
        1,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'silent princess'
        ),
        3,
        null,
        null,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'champion''s tunic'
                AND upgradeable = true
        ),
        2,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'silent princess'
        ),
        3,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'shard of farosh''s horn'
        ),
        2,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'champion''s tunic'
                AND upgradeable = true
        ),
        3,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'silent princess'
        ),
        3,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'shard of naydra''s horn'
        ),
        2,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'champion''s tunic'
                AND upgradeable = true
        ),
        4,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'silent princess'
        ),
        10,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'shard of dinraal''s horn'
        ),
        2,
        null,
        null
    );
