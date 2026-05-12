CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    raw_html TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    previous_version_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    change_types TEXT NOT NULL,
    summary TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    FOREIGN KEY (version_id) REFERENCES versions(id),
    FOREIGN KEY (previous_version_id) REFERENCES versions(id)
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    version_id INTEGER,
    change_id INTEGER,
    FOREIGN KEY (version_id) REFERENCES versions(id),
    FOREIGN KEY (change_id) REFERENCES changes(id)
);

CREATE INDEX IF NOT EXISTS idx_versions_url_created_at ON versions(url, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_changes_detected_at ON changes(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_scans_scanned_at ON scans(scanned_at DESC);
