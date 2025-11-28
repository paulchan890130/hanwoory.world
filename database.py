# database.py

import sqlite3
import hashlib

DB_PATH = "users.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            password_hash TEXT,
            name TEXT,
            phone TEXT,
            folder_id TEXT
        )
    """)
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(email, password, name, phone, folder_id, customer_sheet_id, task_sheet_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO users (email, password_hash, name, phone, folder_id)
        VALUES (?, ?, ?, ?, ?)
    """, (email, hash_password(password), name, phone, folder_id))
    conn.commit()
    conn.close()

def get_user_by_email(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = c.fetchone()
    conn.close()
    return user

def verify_user(email, password):
    user = get_user_by_email(email)
    if user:
        return hash_password(password) == user[1]
    return False
