import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

database_url = os.getenv("database_url")

def get_db_connection():
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except Exception as e:
        print("Error connecting to the database:", str(e))
        return None
    
def close_db_connection(conn):
    if conn:
        try:
            conn.close()
        except Exception as e:
            print("Error closing the database connection:", str(e))

def execute_query(query, params=None):
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        conn.commit()
        return cursor
    except Exception as e:
        print("Error executing query:", str(e))
        return None


query = "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_name = 'ascala_connections' ORDER BY ordinal_position;"

response = execute_query(query)
if response:
    columns = response.fetchall()
    print("Current schema:")
    for col in columns:
        print(f"  {col[0]}: {col[1]} (nullable: {col[2]})")

# Make location_id nullable for bulk installations (if not already)
alter_query = "ALTER TABLE ascala_connections ALTER COLUMN location_id DROP NOT NULL;"
print("\nAttempting to make location_id nullable...")
result = execute_query(alter_query)
if result:
    print("Successfully made location_id nullable!")
    
    # Verify the change
    response2 = execute_query(query)
    if response2:
        print("\nUpdated schema:")
        for col in response2.fetchall():
            if col[0] == 'location_id':
                print(f"  {col[0]}: {col[1]} (nullable: {col[2]})")