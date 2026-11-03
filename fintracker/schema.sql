PRAGMA foreign_keys = ON; 

CREATE TABLE IF NOT EXISTS accounts
( 
  id       INTEGER PRIMARY KEY,
  name     TEXT NOT NULL UNIQUE,
  currency TEXT NOT NULL DEFAULT 'USD' 
);

CREATE TABLE IF NOT EXISTS categories 
( 
  id       INTEGER PRIMARY KEY,
  name     TEXT NOT NULL UNIQUE,
  type     TEXT NOT NULL CHECK (type IN ('income','expense')) 
);

CREATE TABLE IF NOT EXISTS transactions 
(
  id       INTEGER PRIMARY KEY,
  date 	   TEXT NOT NULL, 
-- FORMAT YYYY-MM-DD 
  amount   INTEGER NOT NULL, 
-- FORMAN CENTS/PENNIES, ALWAYS > 0
  account_id INTEGER NOT NULL REFERENCES
accounts(id),
  category_id INTEGER NOT NULL REFERENCES
categories(id), 
  note     TEXT
);

CREATE TABLE IF NOT EXISTS budgets (
    category_id   INTEGER PRIMARY KEY REFERENCES categories(id),
    monthly_limit INTEGER NOT NULL CHECK (monthly_limit > 0)   -- cents
);
