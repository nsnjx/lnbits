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

-- Add comment
COMMENT ON TABLE user_nwc_configs IS 'Stores NWC configuration for each user';
COMMENT ON COLUMN user_nwc_configs.id IS 'Unique identifier for the NWC config';
COMMENT ON COLUMN user_nwc_configs.user_id IS 'Reference to the user account';
COMMENT ON COLUMN user_nwc_configs.pubkey IS 'User public key (Nostr identifier)';
COMMENT ON COLUMN user_nwc_configs.secret IS 'NWC secret for this user';
COMMENT ON COLUMN user_nwc_configs.relay IS 'NWC relay URL for this user';
COMMENT ON COLUMN user_nwc_configs.created_at IS 'When the config was created';
COMMENT ON COLUMN user_nwc_configs.updated_at IS 'When the config was last updated';
