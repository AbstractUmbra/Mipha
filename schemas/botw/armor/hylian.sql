INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian hood'
                AND upgradeable = true
        ),
        1,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin horn'
        ),
        5,
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
            WHERE item_name = 'hylian hood'
                AND upgradeable = true
        ),
        2,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin horn'
        ),
        8,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin fang'
        ),
        5,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian hood'
                AND upgradeable = true
        ),
        3,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin fang'
        ),
        10,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin guts'
        ),
        5,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian hood'
                AND upgradeable = true
        ),
        4,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin guts'
        ),
        15,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'amber'
        ),
        15,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian tunic'
                AND upgradeable = true
        ),
        1,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin horn'
        ),
        5,
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
            WHERE item_name = 'hylian tunic'
                AND upgradeable = true
        ),
        2,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin horn'
        ),
        8,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin fang'
        ),
        5,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian tunic'
                AND upgradeable = true
        ),
        3,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin fang'
        ),
        10,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin guts'
        ),
        5,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian tunic'
                AND upgradeable = true
        ),
        4,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin guts'
        ),
        15,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'amber'
        ),
        15,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian trousers'
                AND upgradeable = true
        ),
        1,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin horn'
        ),
        5,
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
            WHERE item_name = 'hylian trousers'
                AND upgradeable = true
        ),
        2,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin horn'
        ),
        8,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin fang'
        ),
        5,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian trousers'
                AND upgradeable = true
        ),
        3,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin fang'
        ),
        10,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin guts'
        ),
        5,
        null,
        null
    );
INSERT INTO botw_upgrade_path
VALUES (
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'hylian trousers'
                AND upgradeable = true
        ),
        4,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'bokoblin guts'
        ),
        15,
        (
            SELECT item_id
            FROM botw_items
            WHERE item_name = 'amber'
        ),
        15,
        null,
        null
    );
