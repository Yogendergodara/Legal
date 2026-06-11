-- Seed registry and example legal domains (inactive by default for scaffold)
INSERT INTO seed_sources (domain, url_pattern, category, crawl_frequency, robots_respected, active)
VALUES
    ('livelaw.in', 'https://www.livelaw.in/', 'news', 'daily', true, false),
    ('barandbench.com', 'https://www.barandbench.com/', 'news', 'daily', true, false),
    ('prsindia.org', 'https://prsindia.org/', 'statute', 'weekly', true, false),
    ('egazette.gov.in', 'https://egazette.gov.in/', 'regulator', 'daily', true, false),
    ('sci.gov.in', 'https://main.sci.gov.in/', 'court', 'weekly', true, false)
ON CONFLICT DO NOTHING;
