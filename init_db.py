import os
import psycopg2
from dotenv import load_dotenv

# Load env variables
load_dotenv()

def main():
    default_url = os.getenv("DEFAULT_DB_URL")
    target_db_name = os.getenv("DB_NAME", "kaynetics-postgres")
    
    if not default_url:
        print("Error: DEFAULT_DB_URL not found in environment (.env).")
        return
        
    print(f"Connecting to default database to verify/create database: '{target_db_name}'...")
    try:
        conn = psycopg2.connect(default_url)
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Check if database exists in PostgreSQL catalogs
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (target_db_name,))
        exists = cursor.fetchone()
        
        if not exists:
            print(f"Database '{target_db_name}' does not exist. Creating it now...")
            # Wrap the database name in double-quotes to support hyphens
            cursor.execute(f'CREATE DATABASE "{target_db_name}";')
            print(f"Database '{target_db_name}' created successfully!")
        else:
            print(f"Database '{target_db_name}' already exists.")
            
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"An error occurred during database check/creation: {e}")
        print("Please verify your connection credentials and SSL settings.")

if __name__ == "__main__":
    main()
