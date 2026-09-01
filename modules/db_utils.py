# modules/db_utils.py
import sqlite3

def get_connection(db_path="materials_properties.db"):
    """Create and return a database connection"""
    return sqlite3.connect(db_path)