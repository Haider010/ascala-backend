from fastapi import FastAPI, Request
import requests
import time
import random
import string
import psycopg2
import os
from dotenv import load_dotenv

app = FastAPI()

load_dotenv()

database_url = os.getenv("database_url")
client_id = os.getenv("client_id")
client_secret = os.getenv("client_secret")

@app.get("/")
def root():
    return {"message":"API is working!"}

@app.get("/oauth-callback")
async def oauth_callback(request: Request):
    print("Received OAuth callback with query parameters:", request.query_params)
    code = request.query_params.get("code")
    if not code:
        return {"error": "Authorization code not found in the request."}
    
    # Exchange the authorization code for an access token
    token_url = "https://services.leadconnectorhq.com/oauth/token"

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,   
        "grant_type": "authorization_code",
        "redirect_uri": "http://localhost:8000/oauth-callback",
        "user_type": "Company"
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    response = requests.post(token_url, data=payload, headers=headers)
    print("Token exchange response:", response.status_code, response.text)
    if response.status_code != 200:
        return {"error": "Failed to obtain access token."}
    
    api_key = f"ascala_{int(time.time())}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"

    # Store the API key in the database
    try:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        data = response.json()
        
        # Check for existing connection using company_id and location_id
        # For bulk installations, location_id will be None
        location_id = data.get("locationId")
        company_id = data.get("companyId")
        
        if location_id:
            cursor.execute("SELECT id from ascala_connections WHERE location_id = %s", (location_id,))
        else:
            # For bulk installations, check by company_id
            cursor.execute("SELECT id from ascala_connections WHERE company_id = %s AND location_id IS NULL", (company_id,))
        
        result = cursor.fetchone()

        if result:
            cursor.execute("""
                UPDATE ascala_connections
                SET
                    access_token = %s,
                    refresh_token = %s,
                    token_type = %s,
                    expires_in = %s,
                    scope = %s,
                    refresh_token_id = %s,
                    company_id = %s,
                    location_id = %s,
                    user_id = %s,
                    user_type = %s,
                    is_bulk_installation = %s,
                    updated_at = NOW(),
                    api_key = %s
                WHERE id = %s
            """, (
                data.get("access_token"),
                data.get("refresh_token"),
                data.get("token_type"),
                data.get("expires_in"),
                data.get("scope"),
                data.get("refreshTokenId"),
                data.get("companyId"),
                data.get("locationId"),  
                data.get("userId"),
                data.get("userType"),
                data.get("isBulkInstallation"),
                api_key,
                result[0]
            ))

        else:
            cursor.execute("""
                INSERT INTO ascala_connections (
                    access_token,
                    refresh_token,
                    token_type,
                    expires_in,
                    scope,
                    refresh_token_id,
                    company_id,
                    location_id,
                    user_id,
                    user_type,
                    is_bulk_installation,
                    api_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data.get("access_token"),
                data.get("refresh_token"),
                data.get("token_type"),
                data.get("expires_in"),
                data.get("scope"),
                data.get("refreshTokenId"),
                data.get("companyId"),
                data.get("locationId"),  
                data.get("userId"),
                data.get("userType"),
                data.get("isBulkInstallation"),
                api_key
            ))
        conn.commit()
    except Exception as e:
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

    return {"message": "API key generated and stored successfully.", "api_key": api_key}