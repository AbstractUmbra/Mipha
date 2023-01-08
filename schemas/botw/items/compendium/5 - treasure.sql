INSERT INTO botw_compendium (entry_name, entry_number, upgradeable)
VALUES ('treasure chest',
        391,
        false) ON CONFLICT DO NOTHING;


INSERT INTO botw_compendium (entry_name, entry_number, upgradeable)
VALUES ('ore deposit',
        392,
        false) ON CONFLICT DO NOTHING;


INSERT INTO botw_compendium (entry_name, entry_number, upgradeable)
VALUES ('rare ore deposit',
        393,
        false) ON CONFLICT DO NOTHING;


INSERT INTO botw_compendium (entry_name, entry_number, upgradeable)
VALUES ('luminous stone deposit',
        394,
        false) ON CONFLICT DO NOTHING;
