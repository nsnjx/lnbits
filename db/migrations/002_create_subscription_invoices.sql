-- Migration: Create subscription_invoices table for fee splitting
-- Date: 2024-01-01
-- Description: Stores metadata for subscription invoices with automatic fee splitting

CREATE TABLE IF NOT EXISTS subscription_invoices (
    id TEXT PRIMARY KEY,
    payment_hash TEXT NOT NULL UNIQUE,
    user_pubkey TEXT NOT NULL,
    amount INTEGER NOT NULL,
    fee_percentage INTEGER NOT NULL,
    fee_amount INTEGER NOT NULL,
    net_amount INTEGER NOT NULL,
    admin_address TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending', -- pending, paid, fee_sent, completed, failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMP,
    fee_sent_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_subscription_invoices_payment_hash ON subscription_invoices(payment_hash);
CREATE INDEX IF NOT EXISTS idx_subscription_invoices_user_pubkey ON subscription_invoices(user_pubkey);
CREATE INDEX IF NOT EXISTS idx_subscription_invoices_status ON subscription_invoices(status);
CREATE INDEX IF NOT EXISTS idx_subscription_invoices_created_at ON subscription_invoices(created_at);
