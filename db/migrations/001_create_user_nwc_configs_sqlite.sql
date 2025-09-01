-- Migration: Create user_nwc_configs table for multi-user NWC support
-- Date: 2024-01-01
-- Description: Adds support for storing NWC configuration per user

CREATE TABLE IF NOT EXISTS user_nwc_configs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    pubkey TEXT NOT NULL UNIQUE,
    secret TEXT NOT NULL,
    relay TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES accounts(id) ON DELETE CASCADE
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_user_nwc_configs_user_id ON user_nwc_configs(user_id);
CREATE INDEX IF NOT EXISTS idx_user_nwc_configs_pubkey ON user_nwc_configs(pubkey);
