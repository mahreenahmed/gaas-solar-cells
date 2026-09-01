import sqlite3
import os

# ============================================================
# CONFIGURATION
# ============================================================
DB_PATH = "materials_properties.db"  # Update if needed

# Columns that should exist in the rag_workflow table
REQUIRED_COLUMNS = {
    "article_id": "INTEGER REFERENCES articles(id)",
    "pdf_path": "TEXT",
    "status": "TEXT",
    "started_at": "TIMESTAMP",
    "completed_at": "TIMESTAMP",
    "error_message": "TEXT"
}

# ============================================================
# MAIN SCRIPT
# ============================================================

def create_or_alter_rag_workflow(conn):
    cursor = conn.cursor()
    
    # Check if the table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rag_workflow'")
    if not cursor.fetchone():
        print("ℹ️ Table 'rag_workflow' does not exist. Creating it...")
        # Create the table with all required columns
        cursor.execute("""
            CREATE TABLE rag_workflow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER REFERENCES articles(id),
                pdf_path TEXT,
                status TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                error_message TEXT
            )
        """)
        conn.commit()
        print("✅ Table 'rag_workflow' created successfully.")
        return
    
    # Table exists – check each column
    cursor.execute("PRAGMA table_info(rag_workflow)")
    existing_columns = {col[1] for col in cursor.fetchall()}
    
    for col_name, col_type in REQUIRED_COLUMNS.items():
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE rag_workflow ADD COLUMN {col_name} {col_type}")
                conn.commit()
                print(f"✅ Added column '{col_name}' to rag_workflow")
            except sqlite3.OperationalError as e:
                print(f"❌ Failed to add '{col_name}': {e}")
        else:
            print(f"ℹ️ Column '{col_name}' already exists in rag_workflow")
    
    # Verify final structure
    cursor.execute("PRAGMA table_info(rag_workflow)")
    final_columns = [row[1] for row in cursor.fetchall()]
    print("\n🔍 Final columns in rag_workflow:", ", ".join(final_columns))


def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at: {DB_PATH}")
        print("Please update DB_PATH to point to your database file.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    
    print(f"📂 Connected to database: {DB_PATH}")
    print("=" * 50)
    
    create_or_alter_rag_workflow(conn)
    
    conn.close()
    print("\n✅ Done.")


if __name__ == "__main__":
    main()